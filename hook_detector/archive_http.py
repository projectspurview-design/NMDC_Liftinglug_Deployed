"""Read-only, per-camera access to completed MP4 files."""
import json
import re
from urllib.parse import parse_qs, urlsplit
from .archive_worker import NAME

def serve(handler, manager):
    url = urlsplit(handler.path)
    if url.path not in ("/archive/files","/archive/video"):
        return False
    archive = getattr(manager, "archive", None)
    args = parse_qs(url.query)
    camera = args.get("camera", [""])[0]
    if archive is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",camera):
        handler.send_error(404)
        return True
    directory = archive.root / camera
    if directory.is_symlink():
        handler.send_error(404)
        return True
    if url.path.endswith("/files"):
        items = []
        for p in directory.glob("nmdc-*.mp4"):
            if not p.is_symlink() and NAME.fullmatch(p.name):
                try:
                    items.append((p.stat().st_mtime,p.name))
                except FileNotFoundError:
                    continue
        handler._send_json({"segments":sorted(items,reverse=True),"events":archive.events.recent(camera)})
        return True
    name = args.get("name",[""])[0]
    p = directory / name
    if not NAME.fullmatch(name) or p.is_symlink() or not p.is_file():
        handler.send_error(404)
        return True
    try:
        with p.open("rb") as stream:
            import os
            size = os.fstat(stream.fileno()).st_size
            start,end = 0,size-1
            req = handler.headers.get("Range")
            if req:
                m = re.fullmatch(r"bytes=(\d+)-(\d*)",req)
                if not m or int(m[1]) >= size:
                    handler.send_error(416)
                    return True
                start,end = int(m[1]), min(int(m[2]) if m[2] else end,end)
                if end < start:
                    handler.send_error(416)
                    return True
            handler.send_response(206 if req else 200)
            handler.send_header("Content-Type","video/mp4")
            handler.send_header("Accept-Ranges","bytes")
            handler.send_header("Content-Length",str(end-start+1))
            if req:
                handler.send_header("Content-Range",f"bytes {start}-{end}/{size}")
            handler.end_headers()
            stream.seek(start)
            remaining = end-start+1
            while remaining:
                data = stream.read(min(remaining,262144))
                if not data:
                    break
                handler.wfile.write(data)
                remaining -= len(data)
    except FileNotFoundError:
        handler.send_error(404)
    except ConnectionError:
        pass
    return True
