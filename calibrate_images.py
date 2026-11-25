"""Letterbox site frames to the same geometry used by the runtime."""
import argparse
from pathlib import Path
import cv2
import numpy as np
p = argparse.ArgumentParser()
p.add_argument("source",type=Path)
p.add_argument("output",type=Path)
a = p.parse_args()
if a.output.resolve().is_relative_to(a.source.resolve()):
    p.error("Choose an output folder outside the source folder")
a.output.mkdir(parents=True,exist_ok=True)
images = sorted(f for f in a.source.rglob("*") if f.suffix.lower() in (".png",".jpg",".jpeg"))
if not images:
    p.error("No calibration images found")
for i, path in enumerate(images):
    img=cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Unreadable image: {path}")
    h,w=img.shape[:2]
    scale=min(640/h,640/w)
    rh,rw=max(1,round(h*scale)),max(1,round(w*scale))
    x,y=(640-rw)//2,(640-rh)//2
    canvas=np.full((640,640,3),114,np.uint8)
    canvas[y:y+rh,x:x+rw]=cv2.resize(img,(rw,rh))
    if not cv2.imwrite(str(a.output/f"{i:06d}.png"),canvas):
        raise OSError("Calibration image write failed")
