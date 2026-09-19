"""固定语音预设的加载和公开字段。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_VOICE_PRESETS: dict[str, dict[str, Any]] = {
    "ten_years_review": {
        "label": "十年赛事回顾",
        "mode": "file",
        "file_path": "/agibot/data/var/hal_audio/file",
        "file_name": "十年赛事回顾.wav",
        "priority": 6,
        "priority_weight": 0,
    },
    "ten_years_summary": {
        "label": "总结十年赛事",
        "mode": "file",
        "file_path": "/agibot/data/var/hal_audio/file",
        "file_name": "总结十年赛事.wav",
        "priority": 6,
        "priority_weight": 0,
    },
}


def load_voice_presets(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {key: dict(value) for key, value in DEFAULT_VOICE_PRESETS.items()}
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {key: dict(value) for key, value in DEFAULT_VOICE_PRESETS.items()}
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"语音预设配置无效: {config_path}: {exc}") from exc

    entries = payload.get("presets") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"语音预设配置需要 presets 数组: {config_path}")
    presets: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("语音预设必须是对象")
        preset_id = str(entry.get("id", "")).strip()
        if not preset_id or len(preset_id) > 64:
            raise ValueError("语音预设 id 不能为空且不能超过 64 个字符")
        preset = dict(entry)
        preset.pop("id", None)
        _validate_preset(preset_id, preset)
        presets[preset_id] = preset
    return presets


def public_voice_presets(presets: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"id": preset_id, "label": str(preset.get("label", preset_id)), "mode": str(preset["mode"])}
        for preset_id, preset in presets.items()
    ]


def _validate_preset(preset_id: str, preset: dict[str, Any]) -> None:
    mode = preset.get("mode")
    if mode not in ("tts", "file"):
        raise ValueError(f"语音预设 {preset_id} 的 mode 必须是 tts 或 file")
    if mode == "tts" and not str(preset.get("text", "")).strip():
        raise ValueError(f"语音预设 {preset_id} 缺少 text")
    if mode == "file":
        file_path = str(preset.get("file_path", ""))
        file_name = str(preset.get("file_name", ""))
        if not file_path.startswith("/") or not file_name or Path(file_name).name != file_name:
            raise ValueError(f"语音预设 {preset_id} 需要绝对 file_path 和简单 file_name")
    priority = int(preset.get("priority", 6))
    weight = int(preset.get("priority_weight", 0))
    if not 1 <= priority <= 10 or not 0 <= weight <= 99:
        raise ValueError(f"语音预设 {preset_id} 的优先级范围无效")
    preset["priority"] = priority
    preset["priority_weight"] = weight
