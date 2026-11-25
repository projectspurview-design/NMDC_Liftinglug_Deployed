"""Run export with Ultralytics installed. Run compile inside the DX-COM environment."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parent / "weights"
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["export", "compile"])
parser.add_argument("--calibration", type=Path)
args = parser.parse_args()
for name in ("nmdc-yolo","nmdchook","exp","new_lock 1"):
    if args.action == "export":
        from ultralytics import YOLO
        import onnx
        from onnx import helper, TensorProto
        model = YOLO(str(root / (name+".pt")))
        classes = len(model.names)
        exported = model.export(format="onnx", imgsz=640, batch=1, dynamic=False,
                                               end2end=False, simplify=False, nms=False, opset=13, device="cpu")
        net = onnx.load(exported)
        out = net.graph.output[0]
        shape = [d.dim_value for d in out.type.tensor_type.shape.dim]
        if len(shape) != 3 or shape[0] != 1 or not 4+classes <= shape[1] < 128 or shape[2] < 100:
            raise RuntimeError(f"Unexpected detection export {name}: {shape}")
        if len(net.graph.output)>1 or shape[1]!=4+classes:
            for key,value in (("starts",[0]),("ends",[4+classes]),("axes",[1])):
                net.graph.initializer.append(helper.make_tensor("box_"+key, TensorProto.INT64,[1],value))
            net.graph.node.append(helper.make_node("Slice",[out.name,"box_starts","box_ends","box_axes"],["nmdc_boxes"]))
            del net.graph.output[:]
            net.graph.output.append(helper.make_tensor_value_info("nmdc_boxes",TensorProto.FLOAT,[1,4+classes,shape[2]]))
            intermediate = root / (name+".full.onnx")
            onnx.save(net,str(intermediate))
            onnx.utils.extract_model(str(intermediate),str(exported),[net.graph.input[0].name],["nmdc_boxes"])
        continue
    if not args.calibration or not args.calibration.is_dir():
        parser.error("Supply --calibration DIRECTORY with representative letterboxed 640x640 images")
    import onnx
    graph = onnx.load(str(root / (name+".onnx"))).graph
    build = root / "build" / name
    build.mkdir(parents=True, exist_ok=True)
    count = sum(p.is_file() and p.suffix in (".jpg",".jpeg",".png") for p in args.calibration.rglob("*"))
    if not count:
        parser.error("No calibration images found")
    config = {"calibration_method":"ema", "calibration_num":min(count,100), "inputs":{graph.input[0].name:[1,3,640,640]},
              "default_loader":{"dataset_path":str(args.calibration.resolve()),
              "file_extensions":["jpg","png","jpeg"],"preprocessings":[
              {"convertColor":{"form":"BGR2RGB"}},{"div":{"x":255}},
              {"transpose":{"axis":[2,0,1]}},{"expandDim":{"axis":0}}]}}
    cfg = build / "compile.json"
    cfg.write_text(json.dumps(config,indent=2))
    subprocess.run(["dxcom","-m",str(root/(name+".onnx")),"-c",str(cfg),"-o",str(build)],check=True)
    binaries = list(build.rglob("*.dxnn"))
    if len(binaries)!=1:
        raise RuntimeError(f"Inspect compiler outputs in {build}")
    shutil.copy2(binaries[0], root/(name+".dxnn"))
