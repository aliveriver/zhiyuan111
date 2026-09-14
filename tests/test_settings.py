import json
from pathlib import Path

import pytest

from x2_ps5_teleop.core import HandAction
from x2_ps5_teleop.settings import load_settings


def test_loads_repository_configuration():
    settings = load_settings("config/controller.json")

    assert settings.mapping.square == 2
    assert settings.mapping.triangle == 3
    assert settings.mapping.max_linear_x == 0.12
    assert settings.timeout == 0.5
    assert settings.source == "ps5_demo"
    assert settings.presets[HandAction.RT] == (1002, 2, "right_wave")


def test_rejects_invalid_deadzone(tmp_path):
    data = json.loads(Path("config/controller.json").read_text(encoding="utf-8"))
    data["controller"]["deadzone"] = 1.0
    path = tmp_path / "controller.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="deadzone"):
        load_settings(path)
