"""One stream-copy process per camera, using closed segments for retention."""
import csv
import logging
import os
from pathlib import Path
import re
import sqlite3
import shutil
import subprocess
import threading
import time
import uuid

log = logging.getLogger(__name__)
NAME = re.compile(r"^nmdc-[0-9a-f]{32}-\d+\.mp4$")


class Recorder:
    def __init__(self, root, camera, url, seconds):
        self.directory = root / camera
        self.url = url
        self.seconds = seconds
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.state = "starting"
        self.last_segment = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                if not (self.directory.parent / ".nmdc-archive").is_file():
                    raise OSError("Archive volume unavailable")
                self.directory.mkdir(exist_ok=True)
                pattern = "nmdc-" + uuid.uuid4().hex + "-%06d.partial.mp4"
                with self.lock:
                    if self.stop_event.is_set():
                        return
                    self.process = subprocess.Popen(["ffmpeg","-hide_banner","-loglevel","error",
                        "-rtsp_transport","tcp","-timeout","7000000","-i",self.url,"-map","0:v:0",
                        "-c:v","copy","-f","segment","-segment_time",str(self.seconds),
                        "-reset_timestamps","1","-segment_list_type","csv","-segment_list","pipe:1",
                        pattern],cwd=self.directory,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                        stdin=subprocess.PIPE,text=True)
                self.state = "waiting for first segment"
                for entry in csv.reader(self.process.stdout):
                    file = self.directory / Path(entry[0]).name
                    final = file.with_name(file.name.replace(".partial.mp4",".mp4"))
                    if NAME.fullmatch(final.name) and file.is_file():
                        file.replace(final)
                        self.last_segment = time.time()
                        self.state = "recording"
            except (OSError, ValueError, IndexError):
                log.warning("Recorder failed for %s", self.directory.name)
            finally:
                if self.process:
                    if self.process.poll() is None:
                        self.process.kill()
                    self.process.wait()
            self.state = "reconnecting"
            self.stop_event.wait(3)

    def close(self):
        self.stop_event.set()
        with self.lock:
            proc = self.process
            if proc and proc.poll() is None:
                try:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                    proc.wait(timeout=3)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    proc.kill()
        self.thread.join(10)


def prune(root, days, reserve, now=None):
    cutoff = (time.time() if now is None else now) - days*86400
    files = [(p.stat().st_mtime,p) for p in root.glob("*/nmdc-*.mp4")
             if NAME.fullmatch(p.name) and not p.is_symlink() and not p.parent.is_symlink()]
    for stamp,path in sorted(files):
        usage = shutil.disk_usage(root)
        if stamp >= cutoff and usage.free / usage.total >= reserve:
            break
        if stamp >= cutoff:
            log.warning("Archive reserve requires shortening retention: %s",path.name)
        path.unlink(missing_ok=True)


class Archive:
    def __init__(self, root, settings):
        self.root = Path(root).resolve(strict=True)
        from .event_index import Events
        self.events = Events(
            self.root,
            retention_days=settings.get("event_retention_days", 365),
        )
        self.settings = settings
        self.recorders = {}
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self.cleanup, daemon=True)
        self.worker.start()

    def cleanup(self):
        while not self.stop_event.is_set():
            try:
                self.events.prune(time.time()-86400*self.settings.get("event_retention_days",365))
                prune(self.root,self.settings["retention_days"],self.settings["reserve_percent"]/100)
            except (OSError, sqlite3.Error):
                log.exception("Archive retention failed")
            self.stop_event.wait(60)

    def configure(self, camera, url):
        self.disconnect(camera)
        if url:
            self.recorders[camera] = Recorder(self.root,camera,url,self.settings["segment_seconds"])

    def disconnect(self, camera):
        worker = self.recorders.pop(camera,None)
        if worker:
            worker.close()

    def close(self):
        self.stop_event.set()
        for camera in list(self.recorders):
            self.disconnect(camera)
        self.worker.join(5)
