"""处理断连、超时和软件急停的 IDLE/TELEOP 状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from ..core import HandAction, JoySample, Mapping, Mode


class TeleopState(str, Enum):
    IDLE = "IDLE"
    TELEOP = "TELEOP"
    ESTOP = "ESTOP"
    DISCONNECTED = "DISCONNECTED"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class TeleopOutput:
    state: TeleopState
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    mode: Mode | None = None
    hand_actions: tuple[HandAction, ...] = ()


class TeleopStateMachine:
    """将输入转换为安全的机器人指令；启动时始终处于 IDLE。"""

    def __init__(self, mapping: Mapping | None = None, timeout: float = 0.5):
        self.mapping = mapping or Mapping()
        self.timeout = timeout
        self.state = TeleopState.IDLE
        self.mode = Mode.PASSIVE
        self._previous_buttons: tuple[int, ...] = ()
        self._previous_triggers = {"lt": False, "rt": False}
        self._last_input = 0.0

    def update(self, sample: JoySample, now: float | None = None) -> TeleopOutput:
        now = time.monotonic() if now is None else now
        if not sample.connected:
            self.state = TeleopState.DISCONNECTED
            return self._zero()
        if self._pressed(sample.buttons, self.mapping.emergency_stop):
            self.state = TeleopState.ESTOP
            return self._zero()
        if self.state == TeleopState.ESTOP:
            return self._zero()
        if now - sample.stamp > self.timeout:
            self.state = TeleopState.TIMEOUT
            return self._zero()
        self._last_input = sample.stamp
        if self._rising(sample.buttons, self.mapping.teleop_toggle):
            self.state = TeleopState.TELEOP if self.state != TeleopState.TELEOP else TeleopState.IDLE
        if self.state in (TeleopState.DISCONNECTED, TeleopState.TIMEOUT):
            self.state = TeleopState.IDLE
        if self.state != TeleopState.TELEOP:
            self._update_trigger_edges(sample)
            self._previous_buttons = sample.buttons
            return self._zero()
        mode = self._rising_mode(sample.buttons)
        if mode is not None:
            self.mode = mode
        actions = tuple(self._rising_actions(sample))
        self._previous_buttons = sample.buttons
        return TeleopOutput(
            state=self.state,
            linear_x=self._axis(sample.axes, self.mapping.left_y, invert=True) * self.mapping.max_linear_x,
            linear_y=self._axis(sample.axes, self.mapping.left_x) * self.mapping.max_linear_y,
            angular_z=self._axis(sample.axes, self.mapping.right_x) * self.mapping.max_angular_z,
            mode=mode,
            hand_actions=actions,
        )

    def tick(self, now: float | None = None) -> TeleopOutput:
        now = time.monotonic() if now is None else now
        if self.state == TeleopState.TELEOP and now - self._last_input > self.timeout:
            self.state = TeleopState.TIMEOUT
            return self._zero()
        return TeleopOutput(self.state)

    def clear_estop(self) -> None:
        """急停后必须经过操作员的明确操作才能恢复。"""
        if self.state == TeleopState.ESTOP:
            self.state = TeleopState.IDLE

    def _zero(self) -> TeleopOutput:
        return TeleopOutput(self.state, mode=None, hand_actions=())

    def _pressed(self, buttons: tuple[int, ...], index: int) -> bool:
        return index < len(buttons) and bool(buttons[index])

    def _rising(self, buttons: tuple[int, ...], index: int) -> bool:
        return self._pressed(buttons, index) and not self._pressed(self._previous_buttons, index)

    def _rising_mode(self, buttons: tuple[int, ...]) -> Mode | None:
        for index, mode in ((self.mapping.cross, Mode.PASSIVE), (self.mapping.circle, Mode.DAMPING),
                            (self.mapping.triangle, Mode.JOINT), (self.mapping.square, Mode.RL)):
            if self._rising(buttons, index):
                return mode
        return None

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

    def _update_trigger_edges(self, sample: JoySample) -> None:
        for name, index in (("lt", self.mapping.lt), ("rt", self.mapping.rt)):
            self._previous_triggers[name] = self._trigger_active(sample.axes, index)

    def _trigger_active(self, axes: tuple[float, ...], index: int) -> bool:
        if index >= len(axes):
            return False
        value = axes[index]
        normalized = (value + 1.0) / 2.0 if value < 0 else value
        return normalized >= self.mapping.trigger_threshold

    def _axis(self, axes: tuple[float, ...], index: int, invert: bool = False) -> float:
        value = axes[index] if index < len(axes) else 0.0
        if abs(value) <= self.mapping.deadzone:
            return 0.0
        value = (1 if value > 0 else -1) * (abs(value) - self.mapping.deadzone) / (1 - self.mapping.deadzone)
        return -value if invert else value
