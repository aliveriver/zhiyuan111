"""Position-only hand presets; independent of the existing trajectory file.

Positions use measured ROS radians without left-hand mirroring. The editor's
±π rad bound is an application input bound, not a calibrated hardware limit.
No canned 'grip strength' or torque conversion is inferred for OmniHand.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


def validate_positions(positions: Any) -> list[float]:
    if not isinstance(positions, list) or len(positions) != 10:
        raise ValueError("每只手需要 10 个位置值（rad）")
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           or not math.isfinite(x) or not -math.pi <= x <= math.pi for x in positions):
        raise ValueError("手部位置必须为 −π～π rad 的有限数字；该范围不是硬件限位")
    return [float(x) for x in positions]


def validate_pose(pose: Any) -> dict[str, Any]:
    if not isinstance(pose, dict):
        raise ValueError("手部预设格式无效")
    name = pose.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("预设名称需为 1～80 个字符")
    if pose.get("side") not in ("left", "right"):
        raise ValueError("请选择左手或右手")
    if not isinstance(pose.get("requires_confirmation", False), bool):
        raise ValueError("requires_confirmation 必须为布尔值")
    return {
        "name": name.strip(), "side": pose["side"],
        "positions": validate_positions(pose.get("positions")),
        "requires_confirmation": pose.get("requires_confirmation", False),
    }


class HandPoseStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None
        self.poses: dict[str, dict[str, Any]] = {}
        if self.path and self.path.exists():
            # Fail closed on corrupt data: never overwrite a failed load.
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("手部预设文件版本无效")
            for item in payload["poses"]:
                pose = validate_pose(item)
                if pose["name"] in self.poses:
                    raise ValueError("手部预设文件存在重名")
                self.poses[pose["name"]] = pose

    def list(self) -> list[dict[str, Any]]:
        return [dict(p, positions=list(p["positions"]))
                for _, p in sorted(self.poses.items())]

    def save(self, value: Any) -> None:
        pose = validate_pose(value)
        if pose["name"] in self.poses:
            raise ValueError("手部预设名称已存在，不会覆盖")
        self._commit({**self.poses, pose["name"]: pose})

    def delete(self, name: str) -> None:
        if name not in self.poses:
            raise ValueError("手部预设不存在")
        self._commit({key: value for key, value in self.poses.items() if key != name})

    def rename(self, name: str, new_name: str) -> None:
        if name not in self.poses:
            raise ValueError("手部预设不存在")
        pose = validate_pose({**self.poses[name], "name": new_name})
        if pose["name"] != name and pose["name"] in self.poses:
            raise ValueError("手部预设名称已存在，不会覆盖")
        updated = {key: value for key, value in self.poses.items() if key != name}
        updated[pose["name"]] = pose
        self._commit(updated)

    def _commit(self, updated: dict[str, dict[str, Any]]) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                                 dir=self.path.parent, delete=False) as file:
                    temp_name = file.name
                    json.dump({"version": 1, "poses": list(updated.values())}, file,
                              ensure_ascii=False, allow_nan=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp_name, self.path)
            finally:
                if temp_name and os.path.exists(temp_name):
                    os.unlink(temp_name)
        self.poses = updated
