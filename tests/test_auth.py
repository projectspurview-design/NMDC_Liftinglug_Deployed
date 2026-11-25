import json
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from hook_detector import api
from hook_detector.auth import AuthStore, SESSION_COOKIE_NAME
from hook_detector.camera_admin import CameraAdminStore
from hook_detector.event_index import Events
from hook_detector.web_ui import start_backend_server


ADMIN_ID = "nmdc-admin"
ADMIN_PASSWORD = "admin-password-strong"
VIEWER_ID = "bay-viewer"
VIEWER_PASSWORD = "viewer-password-strong"


def test_initial_admin_is_hashed_and_bootstrap_is_one_time(tmp_path):
    store = AuthStore(tmp_path, cookie_secure=False)

    account = store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    assert account["role"] == "admin"

    with store._connect() as database:
        password_hash = database.execute(
            "SELECT password_hash FROM accounts WHERE license_id = ?",
            (ADMIN_ID,),
        ).fetchone()[0]
    assert ADMIN_PASSWORD not in password_hash
    assert store.login(ADMIN_ID, "incorrect-password") is None
    assert store.login("missing-account", ADMIN_PASSWORD) is None

    logged_in = store.login(ADMIN_ID, ADMIN_PASSWORD)
    assert logged_in is not None
    user, session_token, csrf_token = logged_in
    assert user["role"] == "admin"
    assert store.get_session(session_token) == {
        "license_id": ADMIN_ID,
        "role": "admin",
        "csrf_token": csrf_token,
    }
    with store._connect() as database:
        stored_token_hash = database.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert session_token not in stored_token_hash

    with pytest.raises(RuntimeError, match="already complete"):
        store.create_initial_admin("second-admin", ADMIN_PASSWORD)


def test_swagger_declares_csrf_header_for_account_creation():
    operation = api.app.openapi()["paths"]["/api/auth/licenses"]["post"]
    csrf_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "X-CSRF-Token"
    )
    assert csrf_parameter["in"] == "header"
    assert csrf_parameter["required"] is True


def test_local_password_reset_revokes_only_target_account_sessions(tmp_path):
    store = AuthStore(tmp_path, cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    store.create_account(VIEWER_ID, VIEWER_PASSWORD, "viewer")
    admin_session = store.login(ADMIN_ID, ADMIN_PASSWORD)
    viewer_session = store.login(VIEWER_ID, VIEWER_PASSWORD)
    assert admin_session is not None
    assert viewer_session is not None

    store.reset_password(ADMIN_ID, "new-admin-password-strong")

    assert store.login(ADMIN_ID, ADMIN_PASSWORD) is None
    assert store.login(ADMIN_ID, "new-admin-password-strong") is not None
    assert store.get_session(admin_session[1]) is None
    assert store.get_session(viewer_session[1]) is not None
    assert store.login(VIEWER_ID, VIEWER_PASSWORD) is not None

    with pytest.raises(ValueError, match="Account not found"):
        store.reset_password("missing-user", "new-admin-password-strong")


def test_api_login_and_role_permissions(tmp_path):
    store = AuthStore(tmp_path, cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    api.app.state.auth_store = store
    api.app.state.processor = SimpleNamespace(
        health_snapshot=lambda: {"ok": True, "camera_connected": True},
        status_snapshot=lambda: {
            "state_key": "HOOK_INSIDE_LUG",
            "hook_engagement_percent": 70,
            "lock_engagement_percent": 70,
            "meter": 70,
        },
        request_stop=lambda: None,
    )

    admin = TestClient(api.app)
    assert admin.get("/api/status").status_code == 401
    assert admin.get("/api/performance").status_code == 401

    login_response = admin.post(
        "/api/auth/login",
        json={"license_id": ADMIN_ID, "password": ADMIN_PASSWORD},
    )
    assert login_response.status_code == 200
    assert SESSION_COOKIE_NAME in login_response.headers["set-cookie"]
    admin_csrf = login_response.json()["csrf_token"]
    assert admin.get("/api/auth/me").json()["user"]["role"] == "admin"
    assert admin.get("/api/status").json() == {
        "state_key": "HOOK_INSIDE_LUG",
        "hook_engagement_percent": 70,
        "lock_engagement_percent": 70,
        "meter": 70,
    }
    performance = admin.get("/api/performance").json()
    assert set(performance) == {
        "timestamp",
        "cpu",
        "memory",
        "temperature",
        "accelerators",
        "inference",
        "history",
    }
    assert "per_core_percent" in performance["cpu"]
    assert "used_bytes" in performance["memory"]
    assert "npu_devices" in performance["accelerators"]
    assert performance["temperature"] is None or "celsius" in performance["temperature"]
    assert len(performance["history"]) == 1

    create_response = admin.post(
        "/api/auth/licenses",
        json={"license_id": VIEWER_ID, "password": VIEWER_PASSWORD, "role": "viewer"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert create_response.status_code == 201
    assert create_response.json()["account"]["role"] == "viewer"
    assert "password" not in create_response.json()["account"]

    viewer = TestClient(api.app)
    viewer_login = viewer.post(
        "/api/auth/login",
        json={"license_id": VIEWER_ID, "password": VIEWER_PASSWORD},
    )
    assert viewer_login.status_code == 200
    viewer_csrf = viewer_login.json()["csrf_token"]
    assert viewer.get("/api/status").status_code == 200
    assert viewer.get("/api/auth/licenses").status_code == 403
    assert viewer.post(
        "/api/auth/licenses",
        json={"license_id": "another-viewer", "password": VIEWER_PASSWORD, "role": "admin"},
        headers={"X-CSRF-Token": viewer_csrf},
    ).status_code == 403

    assert admin.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": admin_csrf},
    ).status_code == 204
    assert admin.get("/api/status").status_code == 401
    assert store.deactivate_account(VIEWER_ID)
    assert store.get_session(viewer.cookies.get(SESSION_COOKIE_NAME)) is None
    with pytest.raises(ValueError, match="last active Admin"):
        store.deactivate_account(ADMIN_ID)


def test_camera_server_allows_viewer_reads_and_denies_admin_routes(tmp_path):
    store = AuthStore(tmp_path / "auth", cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    store.create_account(VIEWER_ID, VIEWER_PASSWORD, "viewer")
    _, token, _ = store.login(VIEWER_ID, VIEWER_PASSWORD)
    processor = SimpleNamespace(
        camera_admin=CameraAdminStore(tmp_path / "camera"),
        camera_manager=None,
        status_snapshot=lambda: {"state_key": "HOOK_INSIDE_LUG"},
        health_snapshot=lambda: {"ok": True},
    )
    server = start_backend_server(
        processor,
        host="127.0.0.1",
        port=0,
        auth_store=store,
    )
    base_url = f"http://127.0.0.1:{server.server_port}"
    viewer_headers = {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}

    try:
        with pytest.raises(HTTPError) as anonymous_error:
            urlopen(f"{base_url}/status", timeout=2)
        assert anonymous_error.value.code == 401

        with urlopen(Request(f"{base_url}/status", headers=viewer_headers), timeout=2) as response:
            assert response.status == 200

        with pytest.raises(HTTPError) as admin_route_error:
            urlopen(
                Request(f"{base_url}/camera-admin/config", headers=viewer_headers),
                timeout=2,
            )
        assert admin_route_error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_onvif_discovery_requires_admin_session_and_csrf(tmp_path, monkeypatch):
    store = AuthStore(tmp_path / "auth", cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    store.create_account(VIEWER_ID, VIEWER_PASSWORD, "viewer")
    _, admin_token, admin_csrf = store.login(ADMIN_ID, ADMIN_PASSWORD)
    _, viewer_token, viewer_csrf = store.login(VIEWER_ID, VIEWER_PASSWORD)
    processor = SimpleNamespace(camera_admin=CameraAdminStore(tmp_path / "camera"))
    devices = [{
        "ip": "192.168.10.24",
        "manufacturer": "ExampleCorp",
        "model": "Generic Camera",
        "onvif_endpoint": "http://192.168.10.24/onvif/device_service",
        "poe_switch_port": None,
    }]
    monkeypatch.setattr("hook_detector.web_ui.discover_onvif_cameras", lambda: devices)
    server = start_backend_server(
        processor,
        host="127.0.0.1",
        port=0,
        auth_store=store,
    )
    url = f"http://127.0.0.1:{server.server_port}/camera-admin/discover"

    try:
        with pytest.raises(HTTPError) as anonymous_error:
            urlopen(Request(url, data=b"", method="POST"), timeout=2)
        assert anonymous_error.value.code == 401

        with pytest.raises(HTTPError) as viewer_error:
            urlopen(
                Request(
                    url,
                    data=b"",
                    method="POST",
                    headers={
                        "Cookie": f"{SESSION_COOKIE_NAME}={viewer_token}",
                        "X-CSRF-Token": viewer_csrf,
                    },
                ),
                timeout=2,
            )
        assert viewer_error.value.code == 403

        with urlopen(
            Request(
                url,
                data=b"",
                method="POST",
                headers={
                    "Cookie": f"{SESSION_COOKIE_NAME}={admin_token}",
                    "X-CSRF-Token": admin_csrf,
                },
            ),
            timeout=2,
        ) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {
                "protocol": "onvif-ws-discovery",
                "devices": devices,
            }
    finally:
        server.shutdown()
        server.server_close()


def test_engagement_summary_and_event_log_api(tmp_path):
    store = AuthStore(tmp_path / "auth", cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    event_store = Events(archive_dir)
    today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(today, time.min, tzinfo=timezone.utc).timestamp()
    event_store.add("Bay-01-Camera-1", today_start + 10, "HOOK_NOT_ENGAGED", False)
    event_store.add("Bay-01-Camera-1", today_start + 20, "HOOK_ENGAGED_TO_LOCK", True)
    event_store.add("Bay-01-Camera-1", today_start + 30, "HOOK_INSIDE_LUG", True)
    event_store.add("Bay-01-Camera-1", today_start + 40, "HOOK_NOT_ENGAGED_TO_LOCK", False)
    event_store.add("Bay-01-Camera-1", today_start + 50, "HOOK_ENGAGED_TO_LOCK", True)
    event_store.add("Bay-02-Camera-2", today_start + 60, "HOOK_ENGAGED_TO_LOCK", True)
    api.app.state.auth_store = store
    api.app.state.processor = SimpleNamespace(
        camera_manager=SimpleNamespace(archive=SimpleNamespace(events=event_store)),
    )

    client = TestClient(api.app)
    login_response = client.post(
        "/api/auth/login",
        json={"license_id": ADMIN_ID, "password": ADMIN_PASSWORD},
    )
    assert login_response.status_code == 200
    start_date = today.isoformat()
    query = f"timezone=UTC&start_date={start_date}&end_date={start_date}"

    summary = client.get(f"/api/engagement/summary?{query}").json()
    assert summary["total_engagements"] == 3
    assert summary["period_totals"]["today"] == 3
    assert summary["by_bay"] == [
        {"bay_id": "Bay-01", "engagements": 2},
        {"bay_id": "Bay-02", "engagements": 1},
    ]

    bay_summary = client.get(f"/api/engagement/summary?{query}&bay_id=Bay-01").json()
    assert bay_summary["total_engagements"] == 2
    assert bay_summary["selected_bay"] == "Bay-01"

    event_log = client.get(
        f"/api/engagement/events?{query}&camera_id=Bay-01-Camera-1&limit=10"
    ).json()["events"]
    assert [event["event_type"] for event in event_log] == [
        "engagement_started",
        "engagement_ended",
        "state_changed",
        "engagement_started",
        "state_changed",
    ]
    assert all(event["camera_id"] == "Bay-01-Camera-1" for event in event_log)

    bay_event_log = client.get(
        f"/api/engagement/events?{query}&bay_id=Bay-01&limit=10"
    ).json()["events"]
    assert len(bay_event_log) == 5
    assert all(event["camera_id"].startswith("Bay-01-Camera-") for event in bay_event_log)

    default_event_log = client.get(
        "/api/engagement/events?timezone=UTC&camera_id=Bay-01-Camera-1"
    ).json()
    assert default_event_log["date_range"]["start_date"] == (today - timedelta(days=14)).isoformat()
    assert default_event_log["date_range"]["end_date"] == today.isoformat()

    rolling_calendar = client.get("/api/engagement/calendar?timezone=UTC").json()
    assert rolling_calendar["date_range"]["start_date"] == (today - timedelta(days=14)).isoformat()
    assert rolling_calendar["date_range"]["end_date"] == today.isoformat()
    assert len(rolling_calendar["days"]) == 15

    calendar = client.get(
        f"/api/engagement/calendar?timezone=UTC&start_date={start_date}&end_date={start_date}"
    ).json()
    assert calendar["total_engagements"] == 3
    assert calendar["days"] == [{"date": start_date, "total_engagements": 3}]
    assert calendar["by_bay"] == [
        {
            "bay_id": "Bay-01",
            "days": [{"date": start_date, "engagements": 2}],
        },
        {
            "bay_id": "Bay-02",
            "days": [{"date": start_date, "engagements": 1}],
        },
    ]

    charging = client.get(f"/api/charging/analysis?{query}").json()
    assert charging["detected_lifts"] == 3
    assert charging["missed"] == 0
    assert charging["recorded_outcomes"] == 3
    assert charging["period_totals"]["today"] == 3
    assert charging["outcome_breakdown"] == {"detected_lifts": 3, "missed": 0}


def test_train_analytics_filters_and_live_status_are_grouped_by_train(tmp_path):
    store = AuthStore(tmp_path / "auth", cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    event_store = Events(archive_dir)
    today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(today, time.min, tzinfo=timezone.utc).timestamp()
    for bay_number in (1, 4, 5, 8):
        event_store.add(
            f"Bay-{bay_number:02d}-Camera-1",
            today_start + bay_number,
            "HOOK_ENGAGED_TO_LOCK",
            True,
        )

    camera_ids = (
        "Bay-01-Camera-1",
        "Bay-04-Camera-2",
        "Bay-05-Camera-1",
        "Bay-08-Camera-2",
    )
    camera_manager = SimpleNamespace(
        archive=SimpleNamespace(events=event_store),
        camera_admin=SimpleNamespace(configured_camera_ids=camera_ids),
        get_status=lambda camera_id: {"camera_connected": True, "camera_id": camera_id},
    )
    api.app.state.auth_store = store
    api.app.state.processor = SimpleNamespace(camera_manager=camera_manager)
    client = TestClient(api.app)
    login_response = client.post(
        "/api/auth/login",
        json={"license_id": ADMIN_ID, "password": ADMIN_PASSWORD},
    )
    assert login_response.status_code == 200
    start_date = today.isoformat()
    query = f"timezone=UTC&start_date={start_date}&end_date={start_date}"

    train_one_summary = client.get(
        f"/api/engagement/summary?{query}&train_id=1"
    ).json()
    assert train_one_summary["total_engagements"] == 2
    assert train_one_summary["selected_train"] == "1"
    assert {row["bay_id"] for row in train_one_summary["by_bay"]} == {"Bay-01", "Bay-04"}

    train_two_calendar = client.get(
        f"/api/engagement/calendar?{query}&train_id=2"
    ).json()
    assert train_two_calendar["total_engagements"] == 2
    assert train_two_calendar["selected_train"] == "2"
    assert {row["bay_id"] for row in train_two_calendar["by_bay"]} == {"Bay-05", "Bay-08"}

    train_one_events = client.get(
        f"/api/engagement/events?{query}&train_id=1"
    ).json()
    assert {event["camera_id"] for event in train_one_events["events"]} == {
        "Bay-01-Camera-1",
        "Bay-04-Camera-1",
    }

    train_one_status = client.get("/api/trains/1/status").json()
    assert [bay["bay_id"] for bay in train_one_status["bays"]] == [
        "Bay-01",
        "Bay-02",
        "Bay-03",
        "Bay-04",
    ]
    assert [camera["camera_id"] for bay in train_one_status["bays"] for camera in bay["cameras"]] == [
        "Bay-01-Camera-1",
        "Bay-04-Camera-2",
    ]

    areas = client.get("/api/areas").json()["areas"]
    assert [area["area_id"] for area in areas] == ["1", "2"]
    assert areas[0]["bays"] == ["Bay-01", "Bay-02", "Bay-03", "Bay-04"]
    assert areas[1]["bays"] == ["Bay-05", "Bay-06", "Bay-07", "Bay-08"]

    area_one_status = client.get("/api/areas/1/status").json()
    assert area_one_status["train_id"] == "1"
    assert [bay["bay_id"] for bay in area_one_status["bays"]] == areas[0]["bays"]

    area_two_summary = client.get(f"/api/engagement/summary?{query}&area_id=2").json()
    assert area_two_summary["selected_area"] == "2"
    assert area_two_summary["total_engagements"] == 2

    assert client.get("/api/areas/3/status").status_code == 400

    assert client.get("/api/trains/3/status").status_code == 400
    assert client.get(f"/api/engagement/summary?{query}&train_id=3").status_code == 400
    assert client.get(
        f"/api/engagement/events?{query}&train_id=1&bay_id=Bay-01"
    ).status_code == 400


def test_poe_assignment_api_requires_admin_and_persists(tmp_path):
    store = AuthStore(tmp_path / "auth", cookie_secure=False)
    store.create_initial_admin(ADMIN_ID, ADMIN_PASSWORD)
    store.create_account(VIEWER_ID, VIEWER_PASSWORD, "viewer")
    camera_store = CameraAdminStore(tmp_path / "camera")
    api.app.state.auth_store = store
    api.app.state.processor = SimpleNamespace(camera_admin=camera_store)
    admin_client = TestClient(api.app)

    login = admin_client.post("/api/auth/login", json={"license_id": ADMIN_ID, "password": ADMIN_PASSWORD})
    csrf = login.json()["csrf_token"]

    viewer_client = TestClient(api.app)
    viewer_login = viewer_client.post("/api/auth/login", json={"license_id": VIEWER_ID, "password": VIEWER_PASSWORD})
    viewer_csrf = viewer_login.json()["csrf_token"]

    denied = viewer_client.post(
        "/api/poe/assignments",
        json={"bay_id": "Bay-01", "side": "left", "ip_address": "192.168.10.24", "poe_switch_port": "Gi1/0/4"},
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert denied.status_code == 403

    saved = admin_client.post(
        "/api/poe/assignments",
        json={"bay_id": "Bay-01", "side": "left", "ip_address": "192.168.10.24", "poe_switch_port": "Gi1/0/4"},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200
    assert saved.json()["assignment"] == {
        "camera_id": "Bay-01-Camera-1",
        "bay_id": "Bay-01",
        "side": "left",
        "ip_address": "192.168.10.24",
        "poe_switch_port": "Gi1/0/4",
    }

    assignments = admin_client.get("/api/poe/assignments").json()["assignments"]
    left = next(row for row in assignments if row["camera_id"] == "Bay-01-Camera-1")
    assert left["ip_address"] == "192.168.10.24"
    assert left["poe_switch_port"] == "Gi1/0/4"

    reloaded = CameraAdminStore(tmp_path / "camera")
    assert reloaded.poe_assignments()["Bay-01-Camera-1"]["poe_switch_port"] == "Gi1/0/4"