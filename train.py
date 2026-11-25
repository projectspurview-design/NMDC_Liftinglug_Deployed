import argparse
from pathlib import Path

from ultralytics import YOLO


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WEIGHTS = [
    BASE_DIR / "weights" / "nmdc-yolo.pt",
    BASE_DIR / "weights" / "nmdchook.pt",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune one or more YOLO hook/container checkpoints on the same dataset"
    )
    parser.add_argument("--data", type=str, required=True, help="YOLO dataset YAML path")
    parser.add_argument(
        "--weights",
        nargs="+",
        type=Path,
        default=DEFAULT_WEIGHTS,
        metavar="PT_FILE",
        help=(
            "One or more initial YOLO weight files. By default, trains both "
            f"{DEFAULT_WEIGHTS[0]} and {DEFAULT_WEIGHTS[1]} separately."
        ),
    )
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--img", type=int, default=640, help="Input image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument(
        "--project",
        type=Path,
        default=BASE_DIR / "runs" / "train",
        help="Output project folder",
    )
    parser.add_argument("--name", type=str, default="hook_detector", help="Run name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_path = args.project.expanduser().resolve()
    multiple_weights = len(args.weights) > 1

    for weights in args.weights:
        weights_path = weights.expanduser()
        if not weights_path.is_absolute():
            weights_path = Path.cwd() / weights_path
        weights_path = weights_path.resolve()
        if not weights_path.is_file():
            raise FileNotFoundError(f"YOLO weights file not found: {weights_path}")

        run_name = f"{args.name}_{weights_path.stem}" if multiple_weights else args.name
        print(f"Training from: {weights_path}")
        model = YOLO(str(weights_path))
        model.train(
            data=args.data,
            epochs=args.epochs,
            imgsz=args.img,
            batch=args.batch,
            project=str(project_path),
            name=run_name,
            pretrained=True,
        )
        best_weights = Path(model.trainer.save_dir) / "weights" / "best.pt"
        print(f"Best weights saved to: {best_weights.resolve()}")


if __name__ == "__main__":
    main()
