"""为手机控制端提供串行、可抢占且带看门狗的控制会话。

WebSocket 本身是并发的，但机器人命令不能并发执行。这里把控制租约、
序列号检查、状态转换和机器人调用放在同一个 asyncio 锁中，确保旧连接
恢复发送时不会覆盖新连接的控制权。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import time
from typing import Any

from ..core import HandAction, Mode
from ..robot.motion import RobotInterface
from ..teleop.state_machine import TeleopState


LOGGER = logging.getLogger(__name__)


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
        # Trajectories are intentionally kept in the bridge process.  A frame
        # is a protocol command plus the elapsed time from recording start;
        # this keeps recordings hardware agnostic and makes them portable
        # between MockRobot and the ROS backend.
        self.trajectories: dict[str, list[dict[str, Any]]] = {}
        self.recording_name: str | None = None
        self.recording_frames: list[dict[str, Any]] = []
        self.recording_started_at: float | None = None

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
            LOGGER.info("控制连接注册 session=%s client=%s replaced=%s", session_id, client_id, old_session or "-")
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
            LOGGER.info("控制连接断开 session=%s", session_id)
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
            if message_type not in ("velocity", "heartbeat"):
                LOGGER.info("收到控制消息 session=%s type=%s sequence=%s", session_id, message_type, sequence)

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
            elif message_type == "hand_target":
                self._hand_target_locked(message, now)
            elif message_type in ("hand_params", "hand_command"):
                LOGGER.warning("收到兼容手部消息类型 %s，按 hand_target 处理", message_type)
                # Accept the field names used by early mobile builds while
                # keeping one validated path for the robot command.
                normalized = dict(message)
                if "joints" not in normalized:
                    normalized["joints"] = normalized.get("parameters", normalized.get("commands"))
                if "side" not in normalized:
                    normalized["side"] = normalized.get("hand", normalized.get("hand_side"))
                self._hand_target_locked(normalized, now)
            elif message_type == "trajectory_list":
                return {"trajectories": self._trajectory_list_locked(), **self._snapshot_locked(now).as_dict()}
            elif message_type == "trajectory_record_start":
                self._trajectory_record_start_locked(message, now)
            elif message_type == "trajectory_record_stop":
                self._trajectory_record_stop_locked(now)
            elif message_type == "trajectory_save":
                self._trajectory_save_locked(message)
            elif message_type == "trajectory_delete":
                self._trajectory_delete_locked(message)
            elif message_type == "trajectory_rename":
                self._trajectory_rename_locked(message)
            elif message_type == "trajectory_play":
                await self._trajectory_play_locked(message, now)
            elif message_type == "estop":
                self._estop_locked()
            elif message_type == "clear_estop":
                self._clear_estop_locked()
            else:
                LOGGER.warning("拒绝未知消息类型 session=%s type=%r sequence=%s", session_id, message_type, sequence)
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
        self._record_frame_locked({"type": "velocity", "forward": clamped[0], "lateral": clamped[1], "angular": clamped[2]}, now)
        self.last_command_at = now

    def _mode_locked(self, mode_value: Any, now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        try:
            mode = Mode(str(mode_value))
        except ValueError as exc:
            raise BridgeError("invalid_mode", "不支持的运动模式") from exc
        self.robot.set_mode(mode)
        self._record_frame_locked({"type": "mode", "mode": mode.value}, now)
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
            "grip": HandAction.R1,
            "open": HandAction.RT,
            "victory": HandAction.L1,
            "thumbs_up": HandAction.LT,
            "握紧": HandAction.R1,
            "张开": HandAction.RT,
            "比个耶": HandAction.L1,
            "点个赞": HandAction.LT,
        }
        try:
            action = action_by_name[str(action_value)]
        except KeyError as exc:
            LOGGER.warning("拒绝未知预设 action=%r，支持=%s", action_value, ",".join(sorted(action_by_name)))
            raise BridgeError("invalid_preset", "不支持的预设动作") from exc
        LOGGER.info("执行手部预设 action=%s mapped=%s", action_value, action.value)
        self._stop_locked()
        self.robot.hand_action(action)
        self._record_frame_locked({"type": "preset", "action": str(action_value)}, now)
        # A preset is a momentary hand command; keep the teleop lease alive so
        # the operator can immediately issue another action.
        self.state = TeleopState.TELEOP
        self.armed = True
        self.last_command_at = now

    def _hand_target_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        side = message.get("side")
        joints = message.get("joints")
        if side not in ("left", "right") or not isinstance(joints, list) or len(joints) != 10:
            raise BridgeError("invalid_hand_target", "hand_target 需要 side 和 10 个关节")
        clean = []
        for index, joint in enumerate(joints):
            if not isinstance(joint, dict):
                raise BridgeError("invalid_hand_target", "关节参数格式无效")
            values = []
            limits = {
                "position": (-1.0, 1.0),
                "velocity": (0.0, 1.0),
                "acceleration": (0.0, 10.0),
                "deceleration": (0.0, 10.0),
                "effort": (-1.0, 1.0),
            }
            for key in ("position", "velocity", "acceleration", "deceleration", "effort"):
                value = self._finite_number(joint.get(key), key)
                low, high = limits[key]
                if value < low or value > high:
                    raise BridgeError("invalid_hand_target", f"{key} 超出安全范围")
                values.append(value)
            if joint.get("index", index) != index:
                raise BridgeError("invalid_hand_target", "关节序号必须连续")
            clean.append((index, *values))
        self._stop_locked()
        self.robot.hand_target(str(side), clean)
        LOGGER.info("执行手部参数 side=%s joints=%d", side, len(clean))
        self._record_frame_locked({"type": "hand_target", "side": str(side), "joints": joints}, now)
        self.state = TeleopState.TELEOP
        self.armed = True
        self.last_command_at = now

    def _trajectory_list_locked(self) -> list[dict[str, Any]]:
        return [{"name": name, "frames": len(frames)} for name, frames in sorted(self.trajectories.items())]

    def _trajectory_record_start_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        name = str(message.get("name", "")).strip()
        if not name or len(name) > 80:
            raise BridgeError("invalid_trajectory", "轨迹名称不能为空且不能超过 80 个字符")
        self.recording_name = name
        self.recording_frames = []
        self.recording_started_at = now

    def _trajectory_record_stop_locked(self, now: float) -> None:
        if self.recording_name is None:
            raise BridgeError("not_recording", "当前没有正在录制的轨迹")
        self.trajectories[self.recording_name] = list(self.recording_frames)
        self.recording_name = None
        self.recording_frames = []
        self.recording_started_at = None

    def _trajectory_save_locked(self, message: dict[str, Any]) -> None:
        name = str(message.get("name", "")).strip()
        frames = message.get("frames")
        if not name or len(name) > 80 or not isinstance(frames, list):
            raise BridgeError("invalid_trajectory", "需要有效名称和 frames 数组")
        if len(frames) > 10000:
            raise BridgeError("invalid_trajectory", "轨迹帧数不能超过 10000")
        clean: list[dict[str, Any]] = []
        for frame in frames:
            if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                raise BridgeError("invalid_trajectory", "轨迹帧格式无效")
            clean.append(dict(frame))
        self.trajectories[name] = clean

    def _trajectory_delete_locked(self, message: dict[str, Any]) -> None:
        name = str(message.get("name", "")).strip()
        if name not in self.trajectories:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        del self.trajectories[name]

    def _trajectory_rename_locked(self, message: dict[str, Any]) -> None:
        old = str(message.get("old_name", "")).strip()
        new = str(message.get("new_name", "")).strip()
        if old not in self.trajectories:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        if not new or len(new) > 80 or (new != old and new in self.trajectories):
            raise BridgeError("invalid_trajectory", "新名称无效或已存在")
        self.trajectories[new] = self.trajectories.pop(old)

    async def _trajectory_play_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        name = str(message.get("name", "")).strip()
        frames = self.trajectories.get(name)
        if frames is None:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        previous_t = 0
        for frame in frames:
            target_t = frame.get("t_ms", previous_t)
            if isinstance(target_t, (int, float)) and target_t >= previous_t:
                await asyncio.sleep(min(10.0, max(0.0, (float(target_t) - previous_t) / 1000.0)))
                previous_t = float(target_t)
            kind = frame.get("type")
            if kind == "velocity":
                self._velocity_locked(frame, now)
            elif kind == "mode":
                self._mode_locked(frame.get("mode"), now)
            elif kind == "preset":
                self._preset_locked(frame.get("action"), now)
            elif kind == "hand_target":
                self._hand_target_locked(frame, now)
        self.last_command_at = now

    def _record_frame_locked(self, payload: dict[str, Any], now: float) -> None:
        if self.recording_name is None or self.recording_started_at is None:
            return
        frame = dict(payload)
        frame["t_ms"] = max(0, int((now - self.recording_started_at) * 1000))
        self.recording_frames.append(frame)

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
