"""Load the dependency-free JSON configuration used by the demo."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .core import HandAction, Mapping


DEFAULT_CONFIG_PATH = Path("config/controller.json")


@dataclass(frozen=True)
class Settings:
    mapping: Mapping
    timeout: float
    controller_name_hint: str
    source: str
    presets: dict[HandAction, tuple[int, int, str]]


def load_settings(path: str | Path = DEFAULT_CONFIG_PATH) -> Settings:
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        controller = data["controller"]
        robot = data["robot"]
        axes = controller["axes"]
        buttons = controller["buttons"]
        presets_data = robot["presets"]
        mapping = Mapping(
            left_x=int(axes["left_x"]),
            left_y=int(axes["left_y"]),
            right_x=int(axes["right_x"]),
            lt=int(axes["left_trigger"]),
            rt=int(axes["right_trigger"]),
            cross=int(buttons["cross"]),
            circle=int(buttons["circle"]),
            square=int(buttons["square"]),
            triangle=int(buttons["triangle"]),
            l1=int(buttons["l1"]),
            r1=int(buttons["r1"]),
            teleop_toggle=int(buttons["options_toggle"]),
            emergency_stop=int(buttons["ps_estop"]),
            deadzone=float(controller["deadzone"]),
            trigger_threshold=float(controller["trigger_threshold"]),
            max_linear_x=float(robot["max_forward_mps"]),
            max_linear_y=float(robot["max_lateral_mps"]),
            max_angular_z=float(robot["max_yaw_rps"]),
        )
        timeout = float(controller["timeout_seconds"])
        presets = {
            action: (
                int(presets_data[action.name.lower()]["motion"]),
                int(presets_data[action.name.lower()]["area"]),
                str(presets_data[action.name.lower()]["name"]),
            )
            for action in HandAction
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件无效: {config_path}: {exc}") from exc

    if not 0.0 <= mapping.deadzone < 1.0:
        raise ValueError("controller.deadzone 必须在 [0, 1) 范围内")
    if not 0.0 <= mapping.trigger_threshold <= 1.0:
        raise ValueError("controller.trigger_threshold 必须在 [0, 1] 范围内")
    if timeout <= 0.0:
        raise ValueError("controller.timeout_seconds 必须大于 0")
    if min(mapping.max_linear_x, mapping.max_linear_y, mapping.max_angular_z) < 0.0:
        raise ValueError("机器人速度上限不能为负数")

    return Settings(
        mapping=mapping,
        timeout=timeout,
        controller_name_hint=str(controller["name_hint"]),
        source=str(robot["source"]),
        presets=presets,
    )
