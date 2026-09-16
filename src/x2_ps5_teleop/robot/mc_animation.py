"""X2 v0.9.7 MC CSV preparation. This module never sends robot commands.

The installed animation runner consumes one row per 2 ms tick, irrespective
of timeMS. Export only the positively identified arm and hand channels.
"""
from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
import hashlib
import io
import math
from typing import Any

ARM_NAMES = tuple(f"{side}_{joint}_joint" for side in ("left", "right") for joint in (
    "shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
    "wrist_yaw", "wrist_pitch", "wrist_roll",
))
HAND_AXES = (
    "thumb_roll", "thumb_abad", "thumb_mcp", "index_abad", "index_pip",
    "middle_pip", "ring_abad", "ring_pip", "pinky_abad", "pinky_pip",
)
HAND_NAMES = tuple(f"{side}_{joint}_joint" for side in ("left", "right") for joint in HAND_AXES)
UPPER_NAMES = ARM_NAMES + HAND_NAMES
TICK_MS = 2
MAX_ROWS = 150_001  # Bound resources: at most five minutes after speed scaling.


def finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("动画位置和时间必须是有限数字")
    return float(value)


def positions(frame: dict[str, Any]) -> dict[str, float]:
    arms = frame.get("arm", [])
    if len(arms) != 14 or {item.get("name") for item in arms} != set(ARM_NAMES):
        raise ValueError("机械臂必须包含实机配置的 14 个唯一关节名称")
    result = {item["name"]: finite(item["position"]) for item in arms}
    for side in ("left", "right"):
        hand = frame.get(f"{side}_hand", [])
        if len(hand) != 10:
            raise ValueError("每只手需要 10 个原始反馈位置")
        for axis, item in zip(HAND_AXES, hand):
            result[f"{side}_{axis}_joint"] = finite(item["position"])
    return result


@dataclass(frozen=True)
class AnimationCSV:
    content: bytes
    rows: int
    duration_ms: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def compile_animation(frames: list[dict[str, Any]], speed: float = 1.0) -> AnimationCSV:
    """Linearly resample signed feedback at the installed runner's 500 Hz.

    No velocities, efforts, gains, head, waist, leg or root-motion columns are
    copied from the recording. Unequal duplicate timestamps are rejected.
    """
    speed = finite(speed)
    if not 0.01 <= speed <= 4:
        raise ValueError("播放速度必须为 0.01～4 倍")
    times: list[float] = []
    samples: list[dict[str, float]] = []
    for frame in frames:
        if frame.get("type") != "upper_body":
            raise ValueError("MC 动画只接受 upper_body 状态帧")
        timestamp = finite(frame.get("t_ms"))
        if timestamp < 0 or (times and timestamp < times[-1]):
            raise ValueError("时间戳必须非负且递增")
        sample = positions(frame)
        if times and timestamp == times[-1]:
            if sample != samples[-1]:
                raise ValueError("同一时间戳含不同姿态，无法确定运动速度")
            continue
        times.append(timestamp)
        samples.append(sample)
    if len(times) < 2 or times[-1] <= times[0]:
        raise ValueError("动画需要至少两个不同时刻的有效状态帧")
    duration = math.ceil((times[-1] - times[0]) / speed / TICK_MS) * TICK_MS
    rows = duration // TICK_MS + 1
    if rows > MAX_ROWS:
        raise ValueError("变速后的动画不能超过五分钟")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["timeMS", *(f"command_pos::{name}" for name in UPPER_NAMES)])
    for tick in range(rows):
        timestamp = min(times[-1], times[0] + tick * TICK_MS * speed)
        right = min(len(times) - 1, bisect.bisect_right(times, timestamp))
        left = max(0, right - 1)
        span = times[right] - times[left]
        ratio = (timestamp - times[left]) / span if span else 0
        values = [samples[left][name] + ratio * (samples[right][name] - samples[left][name])
                  for name in UPPER_NAMES]
        writer.writerow([tick * TICK_MS, *(format(value, ".9g") for value in values)])
    return AnimationCSV(output.getvalue().encode("ascii"), rows, duration)


def hold_animation(frame: dict[str, Any], duration_ms: int = 1000) -> AnimationCSV:
    """Candidate interruption resource: measured pose, never a neutral pose."""
    return compile_animation([
        {**frame, "type": "upper_body", "t_ms": 0},
        {**frame, "type": "upper_body", "t_ms": duration_ms},
    ])
