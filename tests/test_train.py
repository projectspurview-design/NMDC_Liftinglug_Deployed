from pathlib import Path
from types import SimpleNamespace

import app
import hook_detector.config as config
import pytest
import train


def test_default_training_weights_include_both_checkpoints(monkeypatch):
    monkeypatch.setattr("sys.argv", ["train.py", "--data", "data.yaml"])

    args = train.parse_args()

    assert args.weights == [
        train.BASE_DIR / "weights" / "nmdc-yolo.pt",
        train.BASE_DIR / "weights" / "nmdchook.pt",
    ]


def test_default_detection_uses_all_four_named_dxnn_models():
    expected = [
        str(config.WEIGHTS_DIR / "nmdc-yolo.dxnn"),
        str(config.WEIGHTS_DIR / "nmdchook.dxnn"),
        str(config.WEIGHTS_DIR / "exp.dxnn"),
        str(config.WEIGHTS_DIR / "new_lock 1.dxnn"),
    ]

    assert config.MODEL_PATHS == expected
    assert config.MODEL_PATH == expected[0]
    assert app._required_model_paths() == expected


def test_application_does_not_accept_model_overrides(monkeypatch):
    monkeypatch.setattr("sys.argv", ["app.py", "--model", "other.pt"])

    with pytest.raises(SystemExit) as error:
        app.parse_args()
    assert error.value.code == 2


def test_trains_each_checkpoint_and_prints_best_paths(tmp_path, monkeypatch, capsys):
    checkpoints = [tmp_path / "nmdc-yolo.pt", tmp_path / "nmdchook.pt"]
    for checkpoint in checkpoints:
        checkpoint.touch()

    calls = []

    class FakeYOLO:
        def __init__(self, weights):
            self.weights = weights

        def train(self, **kwargs):
            calls.append((self.weights, kwargs))
            self.trainer = SimpleNamespace(
                save_dir=Path(kwargs["project"]) / kwargs["name"]
            )

    monkeypatch.setattr(train, "YOLO", FakeYOLO)
    monkeypatch.setattr(
        "sys.argv",
        [
            "train.py",
            "--data",
            "data.yaml",
            "--weights",
            *(str(checkpoint) for checkpoint in checkpoints),
            "--project",
            str(tmp_path / "runs"),
        ],
    )

    train.main()

    expected_run_names = ["hook_detector_nmdc-yolo", "hook_detector_nmdchook"]
    assert [call[0] for call in calls] == [str(path) for path in checkpoints]
    assert [call[1]["name"] for call in calls] == expected_run_names
    assert all(call[1]["data"] == "data.yaml" for call in calls)

    output = capsys.readouterr().out
    for checkpoint, run_name in zip(checkpoints, expected_run_names):
        assert f"Training from: {checkpoint}" in output
        assert (
            f"Best weights saved to: {tmp_path / 'runs' / run_name / 'weights' / 'best.pt'}"
            in output
        )
