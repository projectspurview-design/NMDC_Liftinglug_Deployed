from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from fastapi.testclient import TestClient
import pytest

from hook_detector import api


def test_fastapi_serves_built_frontend():
    if not (api.FRONTEND_DIST / "index.html").is_file():
        pytest.skip("Frontend build is created by the Linux installer.")

    response = TestClient(api.app).get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_fastapi_proxies_camera_routes_and_streams_headers():
    class BackendHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"{self.path}|{self.headers.get('Cookie', '')}".encode()
            self.send_response(202)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Set-Cookie", "backend-session=forwarded; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
    thread = threading.Thread(target=backend.serve_forever)
    thread.start()
    previous_url = getattr(api.app.state, "backend_url", None)
    api.app.state.backend_url = f"http://127.0.0.1:{backend.server_port}"
    try:
        with TestClient(api.app) as client:
            backend_client = api.app.state.backend_client
            response = client.get(
                "/camera/Bay-01-Camera-1/status?refresh=1",
                headers={"Cookie": "session-token=forwarded"},
            )
            second_response = client.get("/camera/Bay-01-Camera-1/status")
            assert api.app.state.backend_client is backend_client
            assert not backend_client.is_closed
        assert backend_client.is_closed
    finally:
        if previous_url is None:
            del api.app.state.backend_url
        else:
            api.app.state.backend_url = previous_url
        backend.shutdown()
        backend.server_close()
        thread.join()

    assert response.status_code == 202
    assert response.text == "/camera/Bay-01-Camera-1/status?refresh=1|session-token=forwarded"
    assert response.headers["set-cookie"] == "backend-session=forwarded; Path=/"
    assert second_response.status_code == 202
