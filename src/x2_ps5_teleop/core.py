"""与硬件无关的 PS5 映射和安全状态机。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Iterable


class Mode(str, Enum):
    PASSIVE = "PASSIVE_DEFAULT"
    DAMPING = "DAMPING_DEFAULT"
    JOINT = "JOINT_DEFAULT"
    STAND = "STAND_DEFAULT"
    RL = "STAND_DEFAULT"  # 兼容性别名；AimDK 文档将其定义为 STAND_DEFAULT
    LOCOMOTION = "LOCOMOTION_DEFAULT"


class HandAction(str, Enum):
    RT = "rt_preset"
    LT = "lt_preset"
    R1 = "r1_preset"
    L1 = "l1_preset"


class SafetyState(str, Enum):
    ACTIVE = "active"
    STALE = "stale"


@dataclass(frozen=True)
class JoySample:
    axes: tuple[float, ...] = ()
    buttons: tuple[int, ...] = ()
    stamp: float = field(default_factory=time.monotonic)
    connected: bool = True


@dataclass(frozen=True)
class Output:
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    mode: Mode | None = None
    hand_actions: tuple[HandAction, ...] = ()
    safety: SafetyState = SafetyState.ACTIVE


@dataclass(frozen=True)
class Mapping:
    left_x: int = 0
    left_y: int = 1
    right_x: int = 2
    lt: int = 4
    rt: int = 5
    cross: int = 0
    circle: int = 1
    triangle: int = 3
    square: int = 2
    l1: int = 4
    r1: int = 5
    teleop_toggle: int = 9
    emergency_stop: int = 12
    deadzone: float = 0.08
    max_linear_x: float = 0.12
    max_linear_y: float = 0.08
    max_angular_z: float = 0.15
    trigger_threshold: float = 0.75


class Ps5Teleop:
    """将摇杆采样转换为经过安全处理和去抖的遥操作输出。"""

    def __init__(self, mapping: Mapping | None = None, timeout: float = 0.5):
        self.mapping = mapping or Mapping()
        self.timeout = timeout
        self._previous_buttons: tuple[int, ...] = ()
        self._previous_triggers = {"lt": False, "rt": False}
        self.mode = Mode.PASSIVE
        self.safety = SafetyState.STALE
        self._last_stamp = 0.0

    def update(self, sample: JoySample, now: float | None = None) -> Output:
        now = time.monotonic() if now is None else now
        stale = now - sample.stamp > self.timeout
        if stale:
            self.safety = SafetyState.STALE
            return Output(mode=Mode.DAMPING, safety=self.safety)

        self._last_stamp = sample.stamp
        self.safety = SafetyState.ACTIVE
        mode = self._rising_mode(sample.buttons)
        if mode is not None:
            self.mode = mode
        actions = self._rising_actions(sample)
        self._previous_buttons = sample.buttons
        return Output(
            linear_x=self._axis(sample.axes, self.mapping.left_y, invert=True) * self.mapping.max_linear_x,
            linear_y=self._axis(sample.axes, self.mapping.left_x) * self.mapping.max_linear_y,
            angular_z=self._axis(sample.axes, self.mapping.right_x) * self.mapping.max_angular_z,
            mode=mode,
            hand_actions=tuple(actions),
            safety=self.safety,
        )

    def tick(self, now: float | None = None) -> Output:
        now = time.monotonic() if now is None else now
        if now - self._last_stamp > self.timeout:
            self.safety = SafetyState.STALE
            return Output(mode=Mode.DAMPING, safety=self.safety)
        return Output(safety=self.safety)

    def _rising_mode(self, buttons: tuple[int, ...]) -> Mode | None:
        result = None
        for index, mode in ((self.mapping.cross, Mode.PASSIVE), (self.mapping.circle, Mode.DAMPING),
                            (self.mapping.triangle, Mode.JOINT), (self.mapping.square, Mode.RL)):
            if self._rising(buttons, index):
                result = mode
        return result

    def _rising_actions(self, sample: JoySample) -> list[HandAction]:
        actions: list[HandAction] = []
        if self._rising(sample.buttons, self.mapping.l1):
            actions.append(HandAction.L1)
        if self._rising(sample.buttons, self.mapping.r1):
            actions.append(HandAction.R1)
        for name, index, action in (("lt", self.mapping.lt, HandAction.LT), ("rt", self.mapping.rt, HandAction.RT)):
            active = self._trigger_active(sample.axes, index)
            if active and not self._previous_triggers[name]:
                actions.append(action)
            self._previous_triggers[name] = active
        return actions

    def _rising(self, buttons: Iterable[int], index: int) -> bool:
        current = tuple(buttons)
        return index < len(current) and bool(current[index]) and not (index < len(self._previous_buttons) and self._previous_buttons[index])

    def _trigger_active(self, axes: tuple[float, ...], index: int) -> bool:
        if index >= len(axes):
            return False
        value = axes[index]
        normalized = (value + 1.0) / 2.0 if value < 0.0 else value
        return normalized >= self.mapping.trigger_threshold

    def _axis(self, axes: tuple[float, ...], index: int, invert: bool = False) -> float:
        value = axes[index] if index < len(axes) else 0.0
        if abs(value) <= self.mapping.deadzone:
            value = 0.0
        else:
            sign = -1.0 if value < 0 else 1.0
            value = sign * (abs(value) - self.mapping.deadzone) / (1.0 - self.mapping.deadzone)
        return -value if invert else value
