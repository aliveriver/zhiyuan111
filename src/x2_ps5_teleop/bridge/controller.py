"""为手机控制端提供串行、可抢占且带看门狗的控制会话。

WebSocket 本身是并发的，但机器人命令不能并发执行。这里把控制租约、
序列号检查、状态转换和机器人调用放在同一个 asyncio 锁中，确保旧连接
恢复发送时不会覆盖新连接的控制权。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Any

from ..core import HandAction, Mode
from ..robot.motion import RobotInterface
from ..teleop.state_machine import TeleopState


class BridgeError(ValueError):
    """可安全返回给 WebSocket 客户端的协议或状态错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BridgeSnapshot:
    state: TeleopState
    armed: bool
    source: str
    last_command_age_ms: int | None
    robot_connected: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "state",
            "state": self.state.value,
            "armed": self.armed,
            "source": self.source,
            "last_command_age_ms": self.last_command_age_ms,
            "robot_connected": self.robot_connected,
        }


class BridgeController:
    """单控制者手机桥接控制器。

    ``session_id`` 是服务端为每条 TCP/WebSocket 连接生成的随机标识，
    ``client_id`` 是 App 持久在本次运行中的标识。两者都参与控制权判断：
    新会话接管同一个 client_id 时，旧会话的后续帧全部失效。
    """

    def __init__(
        self,
        robot: RobotInterface,
        *,
        source: str = "mobile_app",
        timeout: float = 0.4,
        velocity_limits: tuple[float, float, float] = (0.12, 0.08, 0.15),
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if any(limit < 0 for limit in velocity_limits):
            raise ValueError("速度上限不能为负数")
        self.robot = robot
        self.source = source
        self.timeout = timeout
        self.velocity_limits = velocity_limits
        self.state = TeleopState.IDLE
        self.armed = False
        self.robot_connected = True
        self.owner_session: str | None = None
        self.owner_client: str | None = None
        self.last_sequence = -1
        self.last_command_at: float | None = None
        self.lock = asyncio.Lock()

    async def register(self, session_id: str, client_id: str) -> str | None:
        """注册控制连接，返回需要被关闭的同 client 旧会话。"""
        if not client_id or len(client_id) > 128:
            raise BridgeError("invalid_client", "client_id 无效")
        async with self.lock:
            old_session = None
            if self.owner_session is not None:
                if self.owner_client != client_id:
                    raise BridgeError("busy", "已有另一台设备占用控制权")
                old_session = self.owner_session
                self._stop_locked()
            self.owner_session = session_id
            self.owner_client = client_id
            self.last_sequence = -1
            self.last_command_at = None
            self.state = TeleopState.IDLE
            self.armed = False
            return old_session

    async def disconnect(self, session_id: str) -> bool:
        async with self.lock:
            if session_id != self.owner_session:
                return False
            self._stop_locked()
            self.state = TeleopState.DISCONNECTED
            self.armed = False
            self.owner_session = None
            self.owner_client = None
            self.last_command_at = None
            return True

    async def snapshot(self, now: float | None = None) -> BridgeSnapshot:
        async with self.lock:
            return self._snapshot_locked(now)

    async def handle(self, session_id: str, message: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        async with self.lock:
            if session_id != self.owner_session:
                raise BridgeError("not_owner", "该连接已失去控制权")
            message_type = message.get("type")
            if message_type == "hello":
                raise BridgeError("duplicate_hello", "连接已经完成 hello")
            sequence = self._sequence(message)
            if sequence <= self.last_sequence:
                raise BridgeError("stale_sequence", "消息序列号必须严格递增")
            self.last_sequence = sequence

            if message_type == "heartbeat":
                self._touch_locked(now)
            elif message_type == "arm":
                self._arm_locked(message.get("enabled"), now)
            elif message_type == "velocity":
                self._velocity_locked(message, now)
            elif message_type == "mode":
                self._mode_locked(message.get("mode"), now)
            elif message_type == "preset":
                self._preset_locked(message.get("action"), now)
            elif message_type == "estop":
                self._estop_locked()
            elif message_type == "clear_estop":
                self._clear_estop_locked()
            else:
                raise BridgeError("unknown_type", f"不支持的消息类型: {message_type!r}")

            return self._snapshot_locked(now).as_dict()

    async def watchdog(self, now: float | None = None) -> bool:
        """检查控制心跳；返回是否发生了状态变化。"""
        now = time.monotonic() if now is None else now
        async with self.lock:
            if (
                self.owner_session is None
                or not self.armed
                or self.state != TeleopState.TELEOP
                or self.last_command_at is None
                or now - self.last_command_at <= self.timeout
            ):
                return False
            self._stop_locked()
            self.state = TeleopState.TIMEOUT
            self.armed = False
            return True

    async def close(self) -> None:
        async with self.lock:
            self._stop_locked()
            self.owner_session = None
            self.owner_client = None
            self.armed = False
        self.robot.close()

    def _sequence(self, message: dict[str, Any]) -> int:
        sequence = message.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise BridgeError("invalid_sequence", "sequence 必须是非负整数")
        return sequence

    def _touch_locked(self, now: float) -> None:
        if self.armed and self.state == TeleopState.TELEOP:
            self.last_command_at = now

    def _arm_locked(self, enabled: Any, now: float) -> None:
        if not isinstance(enabled, bool):
            raise BridgeError("invalid_arm", "arm.enabled 必须是布尔值")
        if enabled:
            if self.state == TeleopState.ESTOP:
                raise BridgeError("estop_latched", "急停已锁存，请先明确清除急停")
            self.state = TeleopState.TELEOP
            self.armed = True
            self.last_command_at = now
        else:
            self._stop_locked()
            self.state = TeleopState.IDLE
            self.armed = False
            self.last_command_at = None

    def _velocity_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        values = tuple(self._finite_number(message.get(key), key) for key in ("forward", "lateral", "angular"))
        clamped = tuple(max(-limit, min(limit, value)) for value, limit in zip(values, self.velocity_limits))
        self.robot.move(*clamped)
        self.last_command_at = now

    def _mode_locked(self, mode_value: Any, now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        try:
            mode = Mode(str(mode_value))
        except ValueError as exc:
            raise BridgeError("invalid_mode", "不支持的运动模式") from exc
        self.robot.set_mode(mode)
        self.last_command_at = now

    def _preset_locked(self, action_value: Any, now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        action_by_name = {
            "rt_preset": HandAction.RT,
            "lt_preset": HandAction.LT,
            "r1_preset": HandAction.R1,
            "l1_preset": HandAction.L1,
            "right_wave": HandAction.RT,
            "left_wave": HandAction.LT,
            "right_raise": HandAction.R1,
            "left_raise": HandAction.L1,
        }
        try:
            action = action_by_name[str(action_value)]
        except KeyError as exc:
            raise BridgeError("invalid_preset", "不支持的预设动作") from exc
        self._stop_locked()
        self.robot.hand_action(action)
        self.state = TeleopState.IDLE
        self.armed = False
        self.last_command_at = None

    def _estop_locked(self) -> None:
        self._stop_locked()
        self.state = TeleopState.ESTOP
        self.armed = False
        self.last_command_at = None

    def _clear_estop_locked(self) -> None:
        if self.state == TeleopState.ESTOP:
            self.state = TeleopState.IDLE

    def _stop_locked(self) -> None:
        self.robot.stop()

    def _snapshot_locked(self, now: float | None = None) -> BridgeSnapshot:
        now = time.monotonic() if now is None else now
        age = None if self.last_command_at is None else max(0, int((now - self.last_command_at) * 1000))
        return BridgeSnapshot(self.state, self.armed, self.source, age, self.robot_connected)

    @staticmethod
    def _finite_number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise BridgeError("invalid_velocity", f"{name} 必须是有限数字")
        return float(value)
