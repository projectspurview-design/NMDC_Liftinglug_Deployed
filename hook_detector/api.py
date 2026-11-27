from __future__ import annotations

from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
import threading
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, AsyncGenerator

import cv2
import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, AuthStore
from .performance import PerformanceMonitor


def _create_backend_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    client = _create_backend_client()
    application.state.backend_client = client
    try:
        yield
    finally:
        await client.aclose()
        del application.state.backend_client


app = FastAPI(title="NMDC Lifting Hook Detector API", lifespan=_lifespan)
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "NMDC_frontend" / "dist"
TRAIN_BAY_IDS = {
    "1": tuple(f"Bay-{bay:02d}" for bay in range(1, 5)),
    "2": tuple(f"Bay-{bay:02d}" for bay in range(5, 9)),
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip().rstrip("/")
        for origin in os.getenv("NMDC_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


async def _proxy_backend(request: Request, backend_path: str) -> StreamingResponse:
    backend_url = getattr(
        request.app.state,
        "backend_url",
        os.getenv("NMDC_BACKEND_PROXY_URL", "http://127.0.0.1:8001"),
    ).rstrip("/")
    target = f"{backend_url}/{backend_path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    forwarded_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in {
            "connection",
            "content-length",
            "host",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
        }
    }
    client: httpx.AsyncClient | None = getattr(request.app.state, "backend_client", None)
    owns_client = client is None
    if client is None:
        client = _create_backend_client()
    try:
        upstream_request = client.build_request(
            request.method,
            target,
            headers=forwarded_headers,
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError as error:
        if owns_client:
            await client.aclose()
        raise HTTPException(
            status_code=502,
            detail="The camera backend is unavailable.",
        ) from error

    excluded_response_headers = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }

    async def stream_upstream() -> AsyncGenerator[bytes, None]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            try:
                await upstream.aclose()
            finally:
                if owns_client:
                    await client.aclose()

    result = StreamingResponse(stream_upstream(), status_code=upstream.status_code)
    result.raw_headers.extend(
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in upstream.headers.multi_items()
        if name.lower() not in excluded_response_headers
    )
    return result


@app.api_route("/auth/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_auth(path: str, request: Request):
    return await _proxy_backend(request, f"auth/{path}")


@app.api_route("/system/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_system(path: str, request: Request):
    return await _proxy_backend(request, f"system/{path}")


@app.api_route("/camera-admin/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_camera_admin(path: str, request: Request):
    return await _proxy_backend(request, f"camera-admin/{path}")


@app.api_route("/camera/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_camera(path: str, request: Request):
    return await _proxy_backend(request, f"camera/{path}")


@app.api_route("/status", methods=["GET", "OPTIONS"], include_in_schema=False)
async def proxy_status(request: Request):
    return await _proxy_backend(request, "status")


@app.api_route("/health", methods=["GET", "OPTIONS"], include_in_schema=False)
async def proxy_health(request: Request):
    return await _proxy_backend(request, "health")


@app.api_route("/snapshot.jpg", methods=["GET", "OPTIONS"], include_in_schema=False)
async def proxy_snapshot(request: Request):
    return await _proxy_backend(request, "snapshot.jpg")


@app.api_route("/stream", methods=["GET", "OPTIONS"], include_in_schema=False)
async def proxy_stream(request: Request):
    return await _proxy_backend(request, "stream")


class LoginRequest(BaseModel):
    license_id: str
    password: str


class CreateAccountRequest(BaseModel):
    license_id: str
    password: str
    role: str


class PoEAssignmentRequest(BaseModel):
    bay_id: str
    side: str
    ip_address: str
    poe_switch_port: str | None = None


def _camera_admin(request: Request):
    processor = getattr(request.app.state, "processor", None)
    manager = getattr(processor, "camera_manager", None)
    camera_admin = getattr(processor, "camera_admin", None) or getattr(manager, "camera_admin", None)
    if camera_admin is None:
        raise HTTPException(status_code=503, detail="Camera settings are unavailable.")
    return camera_admin


def _session(request: Request) -> dict[str, str]:
    auth_store: AuthStore | None = getattr(request.app.state, "auth_store", None)
    if auth_store is None:
        raise HTTPException(status_code=503, detail="Authentication is unavailable.")
    session = auth_store.get_session(request.cookies.get(SESSION_COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return session


def _require_csrf(request: Request, session: dict[str, str], csrf_token: str) -> None:
    auth_store: AuthStore = request.app.state.auth_store
    if not auth_store.verify_csrf(session, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token is missing or invalid.")


def _require_admin(session: dict[str, str]) -> None:
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")


def _event_index(request: Request):
    processor = request.app.state.processor
    manager = getattr(processor, "camera_manager", None)
    archive = getattr(manager, "archive", None)
    events = getattr(manager, "event_index", None) or getattr(archive, "events", None)
    if events is None:
        raise HTTPException(status_code=503, detail="Engagement event archive is unavailable.")
    return events


def _bay_id(camera_id: str) -> str:
    return camera_id.rsplit("-Camera-", 1)[0] if "-Camera-" in camera_id else camera_id


def _train_bays(train_id: str | None) -> tuple[str, ...] | None:
    if train_id is None:
        return None
    for area_id in ("1", "2"):
        config = _area_config(area_id)
        if str(config["train_id"]) == train_id:
            return tuple(config["bays"])
    raise HTTPException(status_code=400, detail="train_id must be 1 or 2.")


def _area_config(area_id: str) -> dict[str, Any]:
    try:
        from .deployment import SETTINGS
        configured = SETTINGS.get("areas", {})
        area = configured.get(area_id)
    except (ImportError, AttributeError, KeyError, TypeError, OSError, ValueError):
        area = None
    if area is not None:
        bays = area.get("bays")
        if isinstance(bays, list) and bays:
            return {
                "area_id": area_id,
                "name": str(area.get("name") or f"Area {area_id}"),
                "train_id": str(area.get("train_id") or area_id),
                "bays": tuple(str(bay) for bay in bays),
            }
    if area_id in DEFAULT_AREA_CONFIG:
        return dict(DEFAULT_AREA_CONFIG[area_id])
    raise HTTPException(status_code=400, detail="area_id must be 1 or 2.")


def _area_bays(area_id: str) -> tuple[str, ...]:
    return tuple(_area_config(area_id)["bays"])


def _area_for_bay(bay_id: str) -> str | None:
    for area_id in ("1", "2"):
        if bay_id in _area_bays(area_id):
            return area_id
    return None


def _matches_bay_filter(
    bay: str,
    bay_id: str | None,
    train_bays: tuple[str, ...] | None,
    area_bays: tuple[str, ...] | None = None,
) -> bool:
    return (
        (bay_id is None or bay == bay_id)
        and (train_bays is None or bay in train_bays)
        and (area_bays is None or bay in area_bays)
    )


def _period_bounds(day: date, zone: ZoneInfo) -> tuple[float, float]:
    local_start = datetime.combine(day, time.min, tzinfo=zone)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return local_start.timestamp(), local_end.timestamp()


def _counts_by_bay(
    events,
    start: float,
    end: float,
    bay_id: str | None,
    train_bays: tuple[str, ...] | None = None,
    area_bays: tuple[str, ...] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for camera_id, count in events.engagement_counts(start, end).items():
        bay = _bay_id(camera_id)
        if _matches_bay_filter(bay, bay_id, train_bays, area_bays):
            counts[bay] += count
    return dict(counts)


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    auth_store: AuthStore | None = getattr(request.app.state, "auth_store", None)
    if auth_store is None:
        raise HTTPException(status_code=503, detail="Authentication is unavailable.")
    result = auth_store.login(payload.license_id, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid License ID or password.")
    account, session_token, csrf_token = result
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=auth_store.cookie_secure,
        samesite=auth_store.cookie_samesite,
        path="/",
    )
    return {
        "user": {"license_id": account["license_id"], "role": account["role"]},
        "csrf_token": csrf_token,
    }


@app.get("/api/auth/me")
async def current_user(session: dict[str, str] = Depends(_session)):
    return {
        "user": {"license_id": session["license_id"], "role": session["role"]},
        "csrf_token": session["csrf_token"],
    }


@app.post("/api/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    csrf_token: str = Header(..., alias="X-CSRF-Token"),
    session: dict[str, str] = Depends(_session),
):
    _require_csrf(request, session, csrf_token)
    request.app.state.auth_store.logout(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=request.app.state.auth_store.cookie_secure,
        httponly=True,
        samesite=request.app.state.auth_store.cookie_samesite,
    )
    response.status_code = 204
    return response


@app.get("/api/auth/licenses")
async def list_accounts(session: dict[str, str] = Depends(_session)):
    _require_admin(session)
    return {"accounts": app.state.auth_store.list_accounts()}


@app.post("/api/auth/licenses", status_code=201)
async def create_account(
    payload: CreateAccountRequest,
    request: Request,
    csrf_token: str = Header(..., alias="X-CSRF-Token"),
    session: dict[str, str] = Depends(_session),
):
    _require_admin(session)
    _require_csrf(request, session, csrf_token)
    try:
        account = request.app.state.auth_store.create_account(
            payload.license_id,
            payload.password,
            payload.role,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"account": account}


@app.delete("/api/auth/licenses/{license_id}")
async def deactivate_account(
    license_id: str,
    request: Request,
    csrf_token: str = Header(..., alias="X-CSRF-Token"),
    session: dict[str, str] = Depends(_session),
):
    _require_admin(session)
    _require_csrf(request, session, csrf_token)
    if license_id.casefold() == session["license_id"].casefold():
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    try:
        deactivated = request.app.state.auth_store.deactivate_account(license_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"license_id": license_id, "deactivated": deactivated}


@app.get("/api/health")
async def health(request: Request):
    _session(request)
    processor = request.app.state.processor
    return processor.health_snapshot()


@app.get("/api/status")
async def status(request: Request):
    _session(request)
    processor = request.app.state.processor
    return processor.status_snapshot()


@app.get("/api/trains/{train_id}/status")
async def train_status(train_id: str, request: Request):
    _session(request)
    train_bays = _train_bays(train_id)
    if train_bays is None:
        raise HTTPException(status_code=400, detail="train_id must be 1 or 2.")
    processor = request.app.state.processor
    manager = getattr(processor, "camera_manager", None)
    camera_admin = getattr(manager, "camera_admin", None)
    if manager is None or camera_admin is None:
        raise HTTPException(status_code=503, detail="Camera status is unavailable.")

    cameras_by_bay: dict[str, list[dict[str, object]]] = {
        bay_id: [] for bay_id in train_bays
    }
    for camera_id in camera_admin.configured_camera_ids:
        bay_id = _bay_id(camera_id)
        if bay_id in cameras_by_bay:
            cameras_by_bay[bay_id].append({
                "camera_id": camera_id,
                "status": manager.get_status(camera_id),
            })

    return {
        "train_id": train_id,
        "bays": [
            {"bay_id": bay_id, "cameras": cameras_by_bay[bay_id]}
            for bay_id in train_bays
        ],
    }


@app.get("/api/areas")
async def areas(request: Request):
    _session(request)
    return {
        "areas": [
            {**_area_config(area_id), "bays": list(_area_bays(area_id))}
            for area_id in ("1", "2")
        ]
    }


@app.get("/api/areas/{area_id}/status")
async def area_status(area_id: str, request: Request):
    _session(request)
    config = _area_config(area_id)
    processor = request.app.state.processor
    manager = getattr(processor, "camera_manager", None)
    camera_admin = getattr(manager, "camera_admin", None)
    if manager is None or camera_admin is None:
        raise HTTPException(status_code=503, detail="Camera status is unavailable.")

    bays: list[dict[str, object]] = []
    for bay_id in tuple(config["bays"]):
        cameras = []
        for camera_id in camera_admin.configured_camera_ids:
            if _bay_id(camera_id) != bay_id:
                continue
            camera_status = manager.get_status(camera_id)
            try:
                camera_number = int(camera_id.rsplit("-Camera-", 1)[1])
                side = "left" if camera_number == 1 else "right"
            except (IndexError, ValueError):
                side = "unknown"
            cameras.append({
                "camera_id": camera_id,
                "side": side,
                "hook_engaged": bool(camera_status.get("hook_engaged", False)),
                "camera_connected": bool(camera_status.get("camera_connected", False)),
                "status": camera_status,
            })
        bays.append({
            "bay_id": bay_id,
            "hook_engaged": any(bool(camera["hook_engaged"]) for camera in cameras),
            "cameras": cameras,
        })
    return {
        "area_id": area_id,
        "name": config["name"],
        "train_id": config["train_id"],
        "bays": bays,
    }


@app.get("/api/performance")
async def performance(request: Request):
    _session(request)
    monitor: PerformanceMonitor | None = getattr(request.app.state, "performance_monitor", None)
    if monitor is None:
        monitor = PerformanceMonitor()
        request.app.state.performance_monitor = monitor
    return monitor.snapshot(request.app.state.processor)


@app.get("/api/engagement/summary")
async def engagement_summary(
    request: Request,
    timezone_name: str = Query("UTC", alias="timezone"),
    start_date: date | None = None,
    end_date: date | None = None,
    bay_id: str | None = None,
    train_id: str | None = None,
    area_id: str | None = None,
):
    _session(request)
    train_bays = _train_bays(train_id)
    area_bays = _area_bays(area_id) if area_id is not None else None
    if bay_id is not None and (train_id is not None or area_id is not None):
        raise HTTPException(status_code=400, detail="Use bay_id alone or use train_id/area_id, not both.")
    if train_id is not None and area_id is not None:
        raise HTTPException(status_code=400, detail="Use either area_id or train_id, not both.")

    if bay_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bay_id):
        raise HTTPException(status_code=400, detail="Invalid bay_id.")
    if train_id is not None and train_id not in ("1", "2"):
        raise HTTPException(status_code=400, detail="train_id must be 1 or 2.")
    if area_id is not None and area_id not in ("1", "2"):
        raise HTTPException(status_code=400, detail="area_id must be 1 or 2.")

    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=400, detail="Unknown IANA timezone.") from error

    now = datetime.now(zone)
    today = now.date()
    selected_start = start_date or today.replace(day=1)
    selected_end = end_date or today
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date.")

    events = _event_index(request)
    period_days = {
        "today": today,
        "yesterday": today - timedelta(days=1),
        "this_week": today - timedelta(days=today.weekday()),
        "this_month": today.replace(day=1),
    }
    period_totals = {}
    for name, first_day in period_days.items():
        if name == "today":
            period_start, period_end = _period_bounds(today, zone)
        elif name == "yesterday":
            period_start, period_end = _period_bounds(first_day, zone)
        else:
            period_start, _ = _period_bounds(first_day, zone)
            _, period_end = _period_bounds(today, zone)
        period_totals[name] = sum(
            _counts_by_bay(events, period_start, period_end, bay_id, train_bays, area_bays).values()
        )

    range_start, _ = _period_bounds(selected_start, zone)
    _, range_end = _period_bounds(selected_end, zone)
    by_bay = _counts_by_bay(events, range_start, range_end, bay_id, train_bays, area_bays)
    return {
        "timezone": timezone_name,
        "date_range": {
            "start_date": selected_start.isoformat(),
            "end_date": selected_end.isoformat(),
        },
        "selected_bay": bay_id,
        "selected_train": train_id,
        "selected_area": area_id,
        "period_totals": period_totals,
        "total_engagements": sum(by_bay.values()),
        "by_bay": [
            {"bay_id": bay, "engagements": count}
            for bay, count in sorted(by_bay.items())
        ],
    }


@app.get("/api/charging/analysis")
async def charging_analysis(
    request: Request,
    timezone_name: str = Query("UTC", alias="timezone"),
    start_date: date | None = None,
    end_date: date | None = None,
    bay_id: str | None = None,
    train_id: str | None = None,
    area_id: str | None = None,
):
    """Dashboard data for detected lifts and bay outcomes."""
    _session(request)
    train_bays = _train_bays(train_id)
    area_bays = _area_bays(area_id) if area_id is not None else None
    if bay_id is not None and (train_id is not None or area_id is not None):
        raise HTTPException(status_code=400, detail="Use bay_id alone or use train_id/area_id, not both.")
    if train_id is not None and area_id is not None:
        raise HTTPException(status_code=400, detail="Use either area_id or train_id, not both.")

    if bay_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bay_id):
        raise HTTPException(status_code=400, detail="Invalid bay_id.")
    if train_id is not None and train_id not in ("1", "2"):
        raise HTTPException(status_code=400, detail="train_id must be 1 or 2.")
    if area_id is not None and area_id not in ("1", "2"):
        raise HTTPException(status_code=400, detail="area_id must be 1 or 2.")

    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=400, detail="Unknown IANA timezone.") from error

    now = datetime.now(zone)
    today = now.date()
    selected_start = start_date or today.replace(day=1)
    selected_end = end_date or today
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date.")

    events = _event_index(request)

    def detected_between(first_day: date, last_day: date) -> int:
        range_start, _ = _period_bounds(first_day, zone)
        _, range_end = _period_bounds(last_day, zone)
        return sum(
            _counts_by_bay(events, range_start, range_end, bay_id, train_bays, area_bays).values()
        )

    period_totals = {
        "today": detected_between(today, today),
        "yesterday": detected_between(today - timedelta(days=1), today - timedelta(days=1)),
        "this_week": detected_between(today - timedelta(days=today.weekday()), today),
        "this_month": detected_between(today.replace(day=1), today),
    }

    range_start, _ = _period_bounds(selected_start, zone)
    _, range_end = _period_bounds(selected_end, zone)
    by_bay_counts = _counts_by_bay(events, range_start, range_end, bay_id, train_bays, area_bays)

    if bay_id is not None:
        bays = (bay_id,)
    elif area_bays is not None:
        bays = area_bays
    elif train_bays is not None:
        bays = train_bays
    else:
        bays = tuple(f"Bay-{number:02d}" for number in range(1, 9))
    by_bay = [
        {
            "bay_id": bay,
            "detected_lifts": by_bay_counts.get(bay, 0),
            "missed": 0,
        }
        for bay in bays
    ]

    detected_lifts = sum(row["detected_lifts"] for row in by_bay)
    missed = sum(row["missed"] for row in by_bay)
    return {
        "timezone": timezone_name,
        "date_range": {
            "start_date": selected_start.isoformat(),
            "end_date": selected_end.isoformat(),
        },
        "selected_bay": bay_id,
        "selected_train": train_id,
        "selected_area": area_id,
        "detected_lifts": detected_lifts,
        "missed": missed,
        "recorded_outcomes": detected_lifts + missed,
        "period_totals": period_totals,
        "by_bay": by_bay,
        "bays": by_bay,
        "outcome_breakdown": {
            "detected_lifts": detected_lifts,
            "missed": missed,
        },
    }


@app.get("/api/engagement/events")
async def engagement_events(
    request: Request,
    timezone_name: str = Query("UTC", alias="timezone"),
    start_date: date | None = None,
    end_date: date | None = None,
    camera_id: str | None = None,
    bay_id: str | None = None,
    train_id: str | None = None,
    area_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    _session(request)
    train_bays = _train_bays(train_id)
    area_bays = _area_bays(area_id) if area_id is not None else None
    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=400, detail="Unknown IANA timezone.") from error

    today = datetime.now(zone).date()
    selected_end = end_date or today
    selected_start = start_date or selected_end - timedelta(days=14)
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date.")
    if camera_id and bay_id:
        raise HTTPException(status_code=400, detail="Use either camera_id or bay_id, not both.")
    if train_id is not None and (camera_id is not None or bay_id is not None):
        raise HTTPException(
            status_code=400,
            detail="Use train_id by itself, or use camera_id or bay_id.",
        )
    if bay_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bay_id):
        raise HTTPException(status_code=400, detail="Invalid bay_id.")
    start, _ = _period_bounds(selected_start, zone)
    _, end = _period_bounds(selected_end, zone)
    effective_bay_ids = area_bays if area_bays is not None else train_bays
    events = _event_index(request).transitions(
        start,
        end,
        camera=camera_id,
        bay_id=bay_id,
        bay_ids=effective_bay_ids,
        limit=limit,
    )
    return {
        "timezone": timezone_name,
        "date_range": {
            "start_date": selected_start.isoformat(),
            "end_date": selected_end.isoformat(),
        },
        "selected_bay": bay_id,
        "selected_train": train_id,
        "selected_camera": camera_id,
        "events": events,
        "limit": limit,
    }


@app.get("/api/engagement/calendar")
async def engagement_calendar(
    request: Request,
    days: int = Query(15, ge=1, le=31),
    timezone_name: str = Query("UTC", alias="timezone"),
    start_date: date | None = None,
    end_date: date | None = None,
    bay_id: str | None = None,
    train_id: str | None = None,
    area_id: str | None = None,
):
    _session(request)
    train_bays = _train_bays(train_id)
    area_bays = _area_bays(area_id) if area_id is not None else None
    if bay_id is not None and train_id is not None:
        raise HTTPException(status_code=400, detail="Use either bay_id or train_id, not both.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise HTTPException(status_code=400, detail="Unknown IANA timezone.") from error

    today = datetime.now(zone).date()
    selected_end = end_date or today
    selected_start = start_date or selected_end - timedelta(days=days - 1)
    if selected_start > selected_end:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date.")

    start, _ = _period_bounds(selected_start, zone)
    _, end = _period_bounds(selected_end, zone)
    dates = [
        selected_start + timedelta(days=offset)
        for offset in range((selected_end - selected_start).days + 1)
    ]
    daily_counts: dict[str, dict[str, int]] = {
        current_date.isoformat(): defaultdict(int) for current_date in dates
    }
    for event in _event_index(request).engagement_starts(start, end):
        event_date = datetime.fromtimestamp(event["timestamp"], zone).date().isoformat()
        event_bay = _bay_id(event["camera_id"])
        if _matches_bay_filter(event_bay, bay_id, train_bays, area_bays):
            daily_counts[event_date][event_bay] += 1

    bays = sorted({bay for by_bay_counts in daily_counts.values() for bay in by_bay_counts})
    day_rows = [
        {
            "date": current_date.isoformat(),
            "total_engagements": sum(daily_counts[current_date.isoformat()].values()),
        }
        for current_date in dates
    ]
    return {
        "timezone": timezone_name,
        "date_range": {
            "start_date": selected_start.isoformat(),
            "end_date": selected_end.isoformat(),
        },
        "selected_bay": bay_id,
        "selected_train": train_id,
        "total_engagements": sum(row["total_engagements"] for row in day_rows),
        "days": day_rows,
        "by_bay": [
            {
                "bay_id": bay,
                "days": [
                    {
                        "date": current_date.isoformat(),
                        "engagements": daily_counts[current_date.isoformat()].get(bay, 0),
                    }
                    for current_date in dates
                ],
            }
            for bay in bays
        ],
    }


@app.get("/api/frame")
async def frame(request: Request):
    """Return the latest processed frame as a JPEG image."""
    _session(request)
    processor = request.app.state.processor
    encoded_frame = getattr(processor, "latest_jpeg", None)
    if encoded_frame is not None:
        return Response(content=encoded_frame, media_type="image/jpeg")

    image = getattr(processor, "latest_frame", None)
    if image is None:
        raise HTTPException(
            status_code=404,
            detail=getattr(processor, "camera_error", None) or "No camera frame is available yet.",
        )

    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to encode frame")
    return Response(content=buffer.tobytes(), media_type="image/jpeg")


@app.post("/api/shutdown", status_code=204)
async def shutdown(
    request: Request,
    csrf_token: str = Header(..., alias="X-CSRF-Token"),
):
    session = _session(request)
    _require_admin(session)
    _require_csrf(request, session, csrf_token)
    processor = request.app.state.processor
    processor.request_stop()
    return Response(status_code=204)


@app.get("/api/poe/assignments")
async def poe_assignments(request: Request):
    session = _session(request)
    _require_admin(session)
    camera_admin = _camera_admin(request)
    assignments = camera_admin.poe_assignments()
    return {"assignments": list(assignments.values())}


@app.post("/api/poe/assignments")
async def save_poe_assignment(
    payload: PoEAssignmentRequest,
    request: Request,
    csrf_token: str = Header(..., alias="X-CSRF-Token"),
):
    session = _session(request)
    _require_admin(session)
    _require_csrf(request, session, csrf_token)
    camera_admin = _camera_admin(request)
    try:
        assignment = camera_admin.save_poe_assignment(
            payload.bay_id,
            payload.side,
            payload.ip_address,
            payload.poe_switch_port,
        )
        processor = getattr(request.app.state, "processor", None)
        manager = getattr(processor, "camera_manager", None)
        if manager is not None and hasattr(camera_admin, "camera_configuration"):
            try:
                manager.configure_camera(
                    assignment["camera_id"],
                    camera_admin.camera_configuration(assignment["camera_id"]),
                )
            except RuntimeError:
                pass
    except (ValueError, RuntimeError, OSError) as error:
        status = 400 if isinstance(error, ValueError) else 503
        raise HTTPException(status_code=status, detail=str(error)) from error
    return {"assignment": assignment, "saved": True}


@app.post("/api/poe/scan")
async def poe_scan(request: Request, csrf_token: str = Header(..., alias="X-CSRF-Token")):
    session = _session(request)
    _require_admin(session)
    _require_csrf(request, session, csrf_token)
    from .camera_discovery import discover_onvif_cameras
    try:
        devices = discover_onvif_cameras()
    except OSError as error:
        raise HTTPException(status_code=503, detail=f"ONVIF discovery failed: {error}") from error
    camera_admin = _camera_admin(request)
    assignments = camera_admin.poe_assignments()
    discovered_by_ip = {device["ip"]: device for device in devices if device.get("ip")}
    for assignment in assignments.values():
        ip_address = assignment.get("ip_address")
        if ip_address in discovered_by_ip:
            device = discovered_by_ip[ip_address]
            assignment["discovered"] = True
            assignment["manufacturer"] = device.get("manufacturer")
            assignment["model"] = device.get("model")
            assignment["onvif_endpoint"] = device.get("onvif_endpoint")
    return {
        "protocol": "onvif-ws-discovery",
        "assignments": list(assignments.values()),
        "discovered_devices": devices,
    }


DEFAULT_AREA_CONFIG = {
    "1": {"area_id": "1", "name": "Area 1", "train_id": "1", "bays": tuple(f"Bay-{bay:02d}" for bay in range(1, 5))},
    "2": {"area_id": "2", "name": "Area 2", "train_id": "2", "bays": tuple(f"Bay-{bay:02d}" for bay in range(5, 9))},
}


def start_api(
    processor,
    host: str = "127.0.0.1",
    port: int = 8002,
    auth_store: AuthStore | None = None,
):
    """Start the API server in a background thread."""
    app.state.backend_url = os.getenv(
        "NMDC_BACKEND_PROXY_URL",
        f"http://127.0.0.1:{port - 1}",
    )

    def _run():
        # attach processor to app state so endpoints can access it
        app.state.processor = processor
        app.state.auth_store = auth_store
        app.state.performance_monitor = PerformanceMonitor()
        uvicorn.run(app, host=host, port=port, log_level="info")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
