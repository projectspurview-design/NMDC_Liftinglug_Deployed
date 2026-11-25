import argparse
import getpass
import os
import signal

from hook_detector.api import start_api
from hook_detector.auth import AuthStore
from hook_detector.camera_admin import DEFAULT_CAMERA_ID, CameraAdminStore
from hook_detector.camera_manager import CameraFeedManager
from hook_detector.config import MODEL_PATHS
from hook_detector.recording import DetectionVideoArchive
from hook_detector.stream import HookStreamProcessor
from hook_detector.web_ui import start_backend_server


def _wait_for_camera_configuration(processor, camera_admin) -> bool:
    while not processor.stop_event.is_set() and not camera_admin.camera_configuration_available:
        processor.stop_event.wait(0.25)
    return not processor.stop_event.is_set()


def _required_model_paths() -> list[str]:
    missing = [path for path in MODEL_PATHS if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError("Required DXNN model file not found: " + ", ".join(missing))
    return list(MODEL_PATHS)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NMDC Lifting Hook Detector backend")
    parser.add_argument("--no-tts", action="store_true", help="Disable text-to-speech notifications")
    parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for the backend HTTP services")
    parser.add_argument("--port", type=int, default=8001, help="Port for backend routes; API docs use port + 1")
    parser.add_argument(
        "--bootstrap-admin",
        action="store_true",
        help="Create the first Admin account interactively, then exit",
    )
    parser.add_argument(
        "--reset-password",
        metavar="LICENSE_ID",
        help="Reset one account password locally, then exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    auth_store = AuthStore()
    if args.bootstrap_admin:
        license_id = input("Initial Admin License ID: ").strip()
        password = getpass.getpass("Initial Admin password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        try:
            account = auth_store.create_initial_admin(license_id, password)
        except (ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        print(f"Initial Admin account created: {account['license_id']}")
        return
    if args.reset_password:
        password = getpass.getpass("New password (12+ characters): ")
        confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        try:
            auth_store.reset_password(args.reset_password, password)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(f"Password reset for account: {args.reset_password.strip()}")
        return
    if not auth_store.list_accounts():
        raise SystemExit("No accounts exist. Run `python app.py --bootstrap-admin` first.")

    from hook_detector.deployment import ROOT, SETTINGS
    from hook_detector.archive_worker import Archive
    from hook_detector.deepx_backend import DeepXDetector
    from hook_detector.event_index import Events
    from pathlib import Path
    import shutil
    detector = None
    if SETTINGS["backend"] != "deepx":
        raise ValueError("Only the DeepX backend with .dxnn models is supported")
    model_paths = _required_model_paths()
    detector = DeepXDetector(ROOT / "weights", args.conf)
    continuous = SETTINGS["recording_mode"] == "continuous"
    if SETTINGS["recording_mode"] not in ("events", "continuous"):
        raise ValueError("Unknown recording mode")
    root = (ROOT / Path(SETTINGS["recording_root"])).resolve()
    if not root.is_dir() or not (root / ".nmdc-archive").is_file():
        raise RuntimeError(f"Create or mount the archive directory and place .nmdc-archive inside it: {root}")
    if continuous and not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg must be on PATH")
    archive = Archive(root, SETTINGS) if continuous else None
    event_index = archive.events if archive else Events(
        root,
        retention_days=SETTINGS["event_retention_days"],
    )

    video_archive = None if continuous else DetectionVideoArchive(directory=root)
    processor = HookStreamProcessor(
        detector=detector,
        model_path=model_paths,
        rtsp_url=None,
        enable_tts=not args.no_tts,
        conf=args.conf,
        show_window=False,
        video_archive=video_archive,
        camera_id=DEFAULT_CAMERA_ID,
    )
    print(f"Detection videos are saved to: {root}")
    processor.camera_admin = CameraAdminStore()
    processor.rtsp_url = None
    processor.show_window = False
    processor.camera_manager = CameraFeedManager(processor, processor.camera_admin)
    processor.camera_manager.archive = archive
    processor.camera_manager.event_index = event_index
    for camera_id in processor.camera_admin.configured_camera_ids:
        processor.camera_manager.configure_camera(
            camera_id,
            processor.camera_admin.camera_configuration(camera_id),
        )
    signal.signal(signal.SIGINT, lambda signum, frame: processor.request_stop())
    signal.signal(signal.SIGTERM, lambda signum, frame: processor.request_stop())
    httpd = start_backend_server(
        processor,
        host=args.host,
        port=args.port,
        auth_store=auth_store,
    )
    start_api(
        processor,
        host=args.host,
        port=(args.port + 1),
        auth_store=auth_store,
    )
    try:
        print(f"Backend routes: http://{args.host}:{args.port}")
        print(f"Interactive API docs: http://{args.host}:{args.port + 1}/docs")
        processor.stop_event.wait()
    finally:
        processor.camera_manager.stop()
        if archive:
            archive.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
