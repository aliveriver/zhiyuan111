"""为手机控制端提供串行、可抢占且带看门狗的控制会话。

WebSocket 本身是并发的，但机器人命令不能并发执行。这里把控制租约、
序列号检查、状态转换和机器人调用放在同一个 asyncio 锁中，确保旧连接
恢复发送时不会覆盖新连接的控制权。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
from pathlib import Path
import time
from typing import Any

from ..core import HandAction, Mode
from ..robot.mc_playback import MCPlayback
from ..robot.motion import RobotInterface
from ..teleop.state_machine import TeleopState
from .hand_poses import HandPoseStore, validate_positions


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
    recording_name: str | None = None
    recording_frames: int = 0
    recording_sample_rate_hz: float | None = None
    playback_state: str = "idle"
    playback_name: str | None = None
    playback_progress_ms: int = 0
    playback_duration_ms: int = 0
    playback_error: str | None = None
    control_capabilities: dict[str, Any] = field(default_factory=dict)
    recording_error: str | None = None
    last_hand_command: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "state",
            "state": self.state.value,
            "armed": self.armed,
            "source": self.source,
            "last_command_age_ms": self.last_command_age_ms,
            "robot_connected": self.robot_connected,
            "recording_name": self.recording_name,
            "recording_frames": self.recording_frames,
            "recording_sample_rate_hz": self.recording_sample_rate_hz,
            "playback_state": self.playback_state,
            "playback_name": self.playback_name,
            "playback_progress_ms": self.playback_progress_ms,
            "playback_duration_ms": self.playback_duration_ms,
            "playback_error": self.playback_error,
            "control_capabilities": self.control_capabilities,
            "recording_error": self.recording_error,
            "last_hand_command": self.last_hand_command,
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
        trajectory_path: str | Path | None = None,
        hand_pose_path: str | Path | None = None,
        mc_playback: MCPlayback | None = None,
    ):
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if any(limit < 0 for limit in velocity_limits):
            raise ValueError("速度上限不能为负数")
        self.robot = robot
        self.mc_playback = mc_playback
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
        # Trajectories are persisted as timestamped upper-body state frames.
        # The MockRobot keeps the legacy event fallback so protocol tests and
        # development without ROS remain usable.
        self.trajectory_path = Path(trajectory_path) if trajectory_path else None
        self.trajectories: dict[str, list[dict[str, Any]]] = self._load_trajectories()
        self.hand_poses = HandPoseStore(hand_pose_path)
        self.last_hand_command: str | None = None
        self.recording_error: str | None = None
        self.recording_name: str | None = None
        self.recording_frames: list[dict[str, Any]] = []
        self.recording_started_at: float | None = None
        self.recording_monotonic_started_at: float | None = None
        self.recording_sample_rate_hz = 20.0
        self.recording_task: asyncio.Task | None = None
        self.recording_teach_mode = False
        self.playback_task: asyncio.Task | None = None
        self.playback_paused = False
        self.playback_state = "idle"
        self.playback_name: str | None = None
        self.playback_progress_ms = 0
        self.playback_duration_ms = 0
        self.playback_error: str | None = None

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
                self._cancel_activity_locked()
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
            self._cancel_activity_locked()
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
            elif message_type in ("hand_pose_list", "hand_pose_save", "hand_pose_delete",
                                  "hand_pose_rename", "hand_pose_apply", "hand_positions", "hand_state"):
                extra = self._hand_pose_locked(message, now)
                return {**self._snapshot_locked(now).as_dict(),
                        "hand_poses": self.hand_poses.list(), **extra}
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
                await self._trajectory_command_locked(message, now)
            elif message_type == "estop":
                self._estop_locked()
            elif message_type == "clear_estop":
                self._clear_estop_locked()
            else:
                LOGGER.warning("拒绝未知消息类型 session=%s type=%r sequence=%s", session_id, message_type, sequence)
                raise BridgeError("unknown_type", f"不支持的消息类型: {message_type!r}")

        result = self._snapshot_locked(now).as_dict()
        result["trajectories"] = self._trajectory_list_locked()
        return result

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
            self._cancel_activity_locked()
            self._stop_locked()
            self.state = TeleopState.TIMEOUT
            self.armed = False
            return True

    async def close(self) -> None:
        async with self.lock:
            self._cancel_activity_locked()
            self._stop_locked()
            self.owner_session = None
            self.owner_client = None
            self.armed = False
        try:
            if self.mc_playback:
                await self.mc_playback.close()  # Outside the asyncio control lock.
        finally:
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
            if self._playback_busy():
                raise BridgeError("activity_busy", "MC 活动尚未确认结束，不能重新进入 TELEOP")
            if self.state == TeleopState.ESTOP:
                raise BridgeError("estop_latched", "急停已锁存，请先明确清除急停")
            self.state = TeleopState.TELEOP
            self.armed = True
            self.last_command_at = now
        else:
            self._cancel_activity_locked()
            self._stop_locked()
            self.state = TeleopState.IDLE
            self.armed = False
            self.last_command_at = None

    def _velocity_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        values = tuple(self._finite_number(message.get(key), key) for key in ("forward", "lateral", "angular"))
        clamped = tuple(max(-limit, min(limit, value)) for value, limit in zip(values, self.velocity_limits))
        if self.recording_name is not None or self._playback_busy():
            clamped = (0.0, 0.0, 0.0)
        self.robot.move(*clamped)
        self._record_frame_locked({"type": "velocity", "forward": clamped[0], "lateral": clamped[1], "angular": clamped[2]}, now)
        self.last_command_at = now

    def _mode_locked(self, mode_value: Any, now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        if self.recording_name is not None or self._playback_busy():
            raise BridgeError("activity_busy", "录制或播放期间不能切换运动模式")
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
        self._require_capability("hand_position")
        self._require_hand_idle()
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
        self._require_capability("hand_position")
        self._require_hand_idle()
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
        self.last_hand_command = f"{side}: 旧版参数目标（已发送，非抓稳确认）"
        LOGGER.info("执行手部参数 side=%s joints=%d", side, len(clean))
        self._record_frame_locked({"type": "hand_target", "side": str(side), "joints": joints}, now)
        self.state = TeleopState.TELEOP
        self.armed = True
        self.last_command_at = now

    def control_capabilities(self) -> dict[str, Any]:
        getter = getattr(self.robot, "control_capabilities", None)
        if getter is not None:
            capabilities = dict(getter())
            if self.mc_playback:
                capabilities.update(upper_body_playback=True, playback_backend="mc_animation",
                                    playback_progress_estimated=True,
                                    max_playback_speed=self.mc_playback.profile['max_speed'],
                                    waist_policy=self.mc_playback.profile.get('waist_policy', 'unchanged'),
                                    commissioned=self.mc_playback.profile.get('commissioned', False),
                                    reason=(("MC 回放已配置现场验收报告；"
                                             if self.mc_playback.profile.get('commissioned') else
                                             "MC 回放处于未校验 commissioning 文件的有人值守测试模式；")
                                            + "腰部由 MC 平衡控制；每次播放仍校验站立、状态与起点，进度为估算"))
            return capabilities
        return {"backend": "custom", "hand_position": False,
                "upper_body_playback": False, "teaching": False,
                "reason": "自定义后端未声明执行能力"}

    def _require_capability(self, key: str) -> None:
        # Legacy third-party backends retain their old protocol path. New
        # position-only commands require an explicit backend implementation.
        if not hasattr(self.robot, "control_capabilities"):
            return
        capabilities = self.control_capabilities()
        if not capabilities.get(key):
            raise BridgeError("control_unavailable", capabilities["reason"])

    def _playback_busy(self) -> bool:
        return self.mc_playback.busy if self.mc_playback else self.playback_state in ("playing", "paused")

    def _require_hand_idle(self) -> None:
        if self._playback_busy():
            raise BridgeError("activity_busy", "请先停止轨迹播放，再发送手部目标")

    def _hand_pose_locked(self, message: dict[str, Any], now: float) -> dict[str, Any]:
        kind = message["type"]
        try:
            if kind == "hand_pose_list":
                return {}
            if kind == "hand_pose_save":
                self.hand_poses.save(message)
                return {}
            if kind == "hand_pose_delete":
                self.hand_poses.delete(str(message.get("name", "")))
                return {}
            if kind == "hand_pose_rename":
                self.hand_poses.rename(str(message.get("name", "")), message.get("new_name"))
                return {}
            if kind == "hand_state":
                state = self.robot.upper_body_state()
                if not self._complete_upper_state(state):
                    raise BridgeError("state_unavailable", "尚未收到完整、新鲜的上肢和双手反馈")
                return {"hand_feedback": {side: [j["position"] for j in state[f"{side}_hand"]]
                                          for side in ("left", "right")}}
            if not self.armed or self.state != TeleopState.TELEOP:
                raise BridgeError("not_armed", "请先进入 TELEOP")
            self._require_hand_idle()
            self._require_capability("hand_position")
            if kind == "hand_pose_apply":
                pose = self.hand_poses.poses.get(str(message.get("name", "")))
                if pose is None:
                    raise ValueError("手部预设不存在")
                if pose["requires_confirmation"] and message.get("confirmed") is not True:
                    raise BridgeError("confirmation_required", "请确认选手已接稳，再执行松手预设")
            else:
                pose = message
            side = pose.get("side")
            if side not in ("left", "right"):
                raise ValueError("请选择左手或右手")
            positions = validate_positions(pose.get("positions"))
            setter = getattr(self.robot, "hand_positions", None)
            if setter is None:
                raise BridgeError("control_unavailable", "后端没有手部位置控制实现")
            self._stop_locked()
            try:
                setter(side, positions)
            except Exception as exc:
                raise BridgeError("hand_control_failed", str(exc)) from exc
            self.last_hand_command = f"{side}: {pose.get('name', '位置目标')}（已发送，非抓稳确认）"
            self.last_command_at = now
            return {}
        except BridgeError:
            raise
        except (ValueError, TypeError, OSError) as exc:
            raise BridgeError("invalid_hand_pose", str(exc)) from exc

    def _trajectory_list_locked(self) -> list[dict[str, Any]]:
        result = []
        for name, frames in sorted(self.trajectories.items()):
            timestamps = [frame.get("t_ms", 0) for frame in frames if isinstance(frame.get("t_ms"), (int, float))]
            upper = [frame for frame in frames if frame.get("type") == "upper_body"]
            result.append({
                "name": name,
                "frames": len(frames),
                "duration_ms": int(max(timestamps, default=0)),
                "arm_joints": max((len(frame.get("arm", [])) for frame in upper), default=0),
                "left_hand_joints": max((len(frame.get("left_hand", [])) for frame in upper), default=0),
                "right_hand_joints": max((len(frame.get("right_hand", [])) for frame in upper), default=0),
            })
        return result

    def _trajectory_record_start_locked(self, message: dict[str, Any], now: float) -> None:
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        name = str(message.get("name", "")).strip()
        if not name or len(name) > 80:
            raise BridgeError("invalid_trajectory", "轨迹名称不能为空且不能超过 80 个字符")
        if self.recording_name is not None:
            raise BridgeError("already_recording", f"正在录制 {self.recording_name}")
        if self._playback_busy():
            raise BridgeError("activity_busy", "请先停止当前轨迹播放")
        if name in self.trajectories:
            raise BridgeError("trajectory_exists", "轨迹名称已存在，请换一个名称或先删除旧轨迹")
        rate = self._finite_number(message.get("sample_rate_hz", 20.0), "sample_rate_hz")
        if rate < 1.0 or rate > 100.0:
            raise BridgeError("invalid_trajectory", "采样频率必须在 1 到 100 Hz 之间")
        teach_mode = message.get("teach_mode", False)
        if not isinstance(teach_mode, bool):
            raise BridgeError("invalid_trajectory", "teach_mode 必须是布尔值")
        self.recording_name = name
        self.recording_frames = []
        self.recording_started_at = now
        self.recording_monotonic_started_at = time.monotonic()
        self.recording_sample_rate_hz = rate
        self.recording_teach_mode = teach_mode
        self.recording_error = None
        self._stop_locked()
        if self.recording_teach_mode:
            prepare_teaching = getattr(self.robot, "prepare_upper_body_teaching", None)
            if prepare_teaching is not None:
                try:
                    prepare_teaching()
                except Exception as exc:
                    self.recording_name = None
                    self.recording_started_at = None
                    self.recording_monotonic_started_at = None
                    self.recording_teach_mode = False
                    raise BridgeError("upper_body_unavailable", str(exc)) from exc
        first = self._append_sample_locked(0)
        if first is False and hasattr(self.robot, "prepare_upper_body_teaching"):
            end_teaching = getattr(self.robot, "end_upper_body_teaching", None)
            if self.recording_teach_mode and end_teaching is not None:
                end_teaching()
            self.recording_name = None
            self.recording_started_at = None
            self.recording_monotonic_started_at = None
            self.recording_teach_mode = False
            raise BridgeError("state_unavailable", "尚未收到机械臂真实状态，请稍后重试")
        if self.recording_task:
            self.recording_task.cancel()
        self.recording_task = asyncio.create_task(self._recording_loop())

    def _trajectory_record_stop_locked(self, now: float) -> None:
        if self.recording_name is None:
            raise BridgeError("not_recording", "当前没有正在录制的轨迹")
        if (
            hasattr(self.robot, "prepare_upper_body_teaching")
            and not any(frame.get("type") == "upper_body" for frame in self.recording_frames)
        ):
            raise BridgeError("empty_trajectory", "没有采集到真实上半身状态，轨迹未保存")
        end_teaching = getattr(self.robot, "end_upper_body_teaching", None)
        if self.recording_teach_mode and end_teaching is not None:
            end_teaching()
        self.trajectories[self.recording_name] = list(self.recording_frames)
        self._persist_trajectories_locked()
        if self.recording_task:
            self.recording_task.cancel()
            self.recording_task = None
        self.recording_name = None
        self.recording_frames = []
        self.recording_started_at = None
        self.recording_monotonic_started_at = None
        self.recording_teach_mode = False

    def _trajectory_save_locked(self, message: dict[str, Any]) -> None:
        name = str(message.get("name", "")).strip()
        frames = message.get("frames")
        if not name or len(name) > 80 or not isinstance(frames, list):
            raise BridgeError("invalid_trajectory", "需要有效名称和 frames 数组")
        if name in self.trajectories:
            raise BridgeError("trajectory_exists", "轨迹名称已存在，请换一个名称或先删除旧轨迹")
        if len(frames) > 10000:
            raise BridgeError("invalid_trajectory", "轨迹帧数不能超过 10000")
        clean: list[dict[str, Any]] = []
        for frame in frames:
            if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                raise BridgeError("invalid_trajectory", "轨迹帧格式无效")
            clean.append(dict(frame))
        self.trajectories[name] = clean
        self._persist_trajectories_locked()

    def _trajectory_delete_locked(self, message: dict[str, Any]) -> None:
        name = str(message.get("name", "")).strip()
        if name not in self.trajectories:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        if self._playback_busy() and self.playback_name == name:
            raise BridgeError("activity_busy", "请先停止当前轨迹播放")
        del self.trajectories[name]
        self._persist_trajectories_locked()

    def _trajectory_rename_locked(self, message: dict[str, Any]) -> None:
        old = str(message.get("old_name", "")).strip()
        new = str(message.get("new_name", "")).strip()
        if old not in self.trajectories:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        if self._playback_busy() and self.playback_name == old:
            raise BridgeError("activity_busy", "请先停止当前轨迹播放")
        if not new or len(new) > 80 or (new != old and new in self.trajectories):
            raise BridgeError("invalid_trajectory", "新名称无效或已存在")
        self.trajectories[new] = self.trajectories.pop(old)
        self._persist_trajectories_locked()

    async def _trajectory_command_locked(self, message: dict[str, Any], now: float) -> None:
        command = str(message.get("command", "start"))
        if command not in ("start", "pause", "resume", "stop"):
            raise BridgeError("invalid_trajectory", "未知播放命令")
        if self.mc_playback and command == "stop":
            self.mc_playback.stop()
            self._stop_locked()
            return
        if command == "stop":
            if self.playback_task:
                self.playback_task.cancel()
                self.playback_task = None
            self.playback_paused = False
            self.playback_state = "idle"
            self.playback_name = None
            self._stop_locked()
            return
        if not self.armed or self.state != TeleopState.TELEOP:
            raise BridgeError("not_armed", "请先进入 TELEOP")
        if self.mc_playback and command in ("pause", "resume"):
            try:
                getattr(self.mc_playback, command)()
            except ValueError as exc:
                raise BridgeError("invalid_playback_state", str(exc)) from exc
            self.last_command_at = now
            return
        if command == "pause":
            if self.playback_state != "playing":
                raise BridgeError("not_playing", "当前没有正在播放的轨迹")
            self.playback_paused = True
            self.playback_state = "paused"
            return
        if command == "resume":
            if self.playback_state != "paused":
                raise BridgeError("not_paused", "当前没有暂停的轨迹")
            self.playback_paused = False
            self.playback_state = "playing"
            return
        if self.recording_name is not None:
            raise BridgeError("activity_busy", "请先停止并保存当前录制")
        if self._playback_busy():
            raise BridgeError("activity_busy", "已有轨迹正在播放，请先停止")
        name = str(message.get("name", "")).strip()
        frames = self.trajectories.get(name)
        if frames is None:
            raise BridgeError("trajectory_not_found", "轨迹不存在")
        upper_frames = [frame for frame in frames if frame.get("type") == "upper_body"]
        if not upper_frames:
            if hasattr(self.robot, "prepare_upper_body_playback"):
                raise BridgeError("invalid_trajectory", "轨迹不包含真实上半身状态")
            await self._play_legacy_trajectory_locked(frames, speed=self._finite_number(message.get("speed", 1.0), "speed"), now=now)
            return
        speed = self._finite_number(message.get("speed", 1.0), "speed")
        if speed < 0.01 or speed > 4:
            raise BridgeError("invalid_trajectory", "播放速度必须在 0.01 到 4 倍之间")
        self._require_capability("upper_body_playback")
        previous = -1.0
        for frame in upper_frames:
            timestamp = self._finite_number(frame.get("t_ms"), "t_ms")
            if timestamp < 0 or timestamp < previous or not self._complete_upper_state(frame):
                raise BridgeError("invalid_trajectory", "轨迹需包含顺序时间戳、14 个臂关节及左右手各 10 个有限位置")
            previous = timestamp
        if self.mc_playback:
            try:
                self._stop_locked()
                self.mc_playback.start(upper_frames, speed)
            except ValueError as exc:
                raise BridgeError("invalid_trajectory", str(exc)) from exc
            self.playback_name = name
            self.last_command_at = now
            return
        self.playback_paused = False
        prepare = getattr(self.robot, "prepare_upper_body_playback", None)
        if prepare is not None:
            try:
                prepare(upper_frames[0])
            except Exception as exc:
                raise BridgeError("upper_body_unavailable", str(exc)) from exc
        self._stop_locked()
        self.playback_state = "playing"
        self.playback_name = name
        self.playback_progress_ms = 0
        self.playback_duration_ms = int(max((frame.get("t_ms", 0) for frame in upper_frames), default=0))
        self.playback_error = None
        self.playback_task = asyncio.create_task(self._trajectory_play_locked(upper_frames, speed))
        # The task acquires the same controller lock before each output.

    async def _trajectory_play_locked(self, frames: list[dict[str, Any]], speed: float) -> None:
        previous_t = 0.0
        try:
            for frame in frames:
                target_t = float(frame.get("t_ms", previous_t))
                remaining = max(0.0, (target_t - previous_t) / 1000.0 / speed)
                while remaining > 0:
                    if self.playback_paused:
                        await asyncio.sleep(0.05)
                        continue
                    tick = min(0.02, remaining)
                    await asyncio.sleep(tick)
                    if not self.playback_paused:
                        remaining -= tick
                while self.playback_paused:
                    await asyncio.sleep(0.02)
                async with self.lock:
                    if not self.armed or self.state != TeleopState.TELEOP:
                        return
                    self.robot.upper_body_target(frame)
                    self.playback_progress_ms = int(target_t)
                previous_t = target_t
            self.playback_state = "completed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("轨迹播放失败 name=%s", self.playback_name)
            self.playback_state = "error"
            self.playback_error = str(exc)
        finally:
            if self.playback_task is asyncio.current_task():
                self.playback_task = None

    async def _play_legacy_trajectory_locked(self, frames: list[dict[str, Any]], speed: float, now: float) -> None:
        """Keep protocol-only custom backends usable; X2 never enters this path."""
        if speed <= 0 or speed > 4:
            raise BridgeError("invalid_trajectory", "播放速度必须在 0.01 到 4 倍之间")
        previous_t = 0.0
        for frame in frames:
            target_t = float(frame.get("t_ms", previous_t))
            await asyncio.sleep(max(0.0, (target_t - previous_t) / 1000.0 / speed))
            kind = frame.get("type")
            if kind == "preset":
                self._preset_locked(frame.get("action"), now)
            elif kind == "hand_target":
                self._hand_target_locked(frame, now)
            previous_t = target_t

    def _record_frame_locked(self, payload: dict[str, Any], now: float) -> None:
        if self.recording_name is None or self.recording_started_at is None:
            return
        frame = dict(payload)
        frame["t_ms"] = max(0, int((now - self.recording_started_at) * 1000))
        if payload.get("type") in {"velocity", "mode"}:
            return
        if payload.get("type") not in {"upper_body"}:
            sampled = self._append_sample_locked(frame["t_ms"])
            if sampled is None:
                # Compatibility fallback for mock/custom backends that do not
                # expose ROS state sampling; real X2 recordings stay state-only.
                self.recording_frames.append(frame)
        else:
            self.recording_frames.append(frame)

    def _append_sample_locked(self, t_ms: int) -> bool | None:
        sampler = getattr(self.robot, "upper_body_state", None)
        if sampler is None:
            return None
        state = sampler()
        if self._complete_upper_state(state):
            self.recording_frames.append({"type": "upper_body", "t_ms": t_ms,
                                          **{key: state[key] for key in ("arm", "left_hand", "right_hand")}})
            return True
        return False

    @staticmethod
    def _complete_upper_state(state: Any) -> bool:
        if not isinstance(state, dict):
            return False
        for key, count in (("arm", 14), ("left_hand", 10), ("right_hand", 10)):
            joints = state.get(key)
            if not isinstance(joints, list) or len(joints) != count:
                return False
            for joint in joints:
                if not isinstance(joint, dict) or not isinstance(joint.get("name"), str):
                    return False
                for field in ("position", "velocity", "effort"):
                    value = joint.get(field, 0.0) if field != "position" else joint.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        return False
        return True

    async def _recording_loop(self) -> None:
        """采样实际状态，而不是把手机发出的目标值当作轨迹。"""
        try:
            next_sample = time.monotonic() + 1.0 / self.recording_sample_rate_hz
            while self.recording_name is not None:
                # Teach damping is refreshed at 100 Hz while state sampling
                # remains independently configurable by the App.
                await asyncio.sleep(0.01 if self.recording_teach_mode else min(0.05, 1.0 / self.recording_sample_rate_hz))
                async with self.lock:
                    if self.recording_name is None or self.recording_started_at is None:
                        return
                    if self.recording_teach_mode:
                        teach = getattr(self.robot, "upper_body_teaching_step", None)
                        if teach is not None:
                            teach()
                    current = time.monotonic()
                    if current < next_sample:
                        continue
                    started = self.recording_monotonic_started_at or time.monotonic()
                    t_ms = max(0, int((current - started) * 1000))
                    if not self._append_sample_locked(t_ms):
                        self.recording_error = "上肢反馈缺失或过期，已跳过该采样；停止后可保存已有帧"
                    next_sample += 1.0 / self.recording_sample_rate_hz
                    if next_sample < current:
                        next_sample = current + 1.0 / self.recording_sample_rate_hz
        except asyncio.CancelledError:
            return
        except Exception as exc:
            LOGGER.exception("上肢状态采样失败")
            self.recording_error = f"采样已中止：{exc}；停止录制可保存已有帧"

    def _load_trajectories(self) -> dict[str, list[dict[str, Any]]]:
        if not self.trajectory_path or not self.trajectory_path.exists():
            return {}
        try:
            payload = json.loads(self.trajectory_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            LOGGER.warning("无法读取轨迹文件: %s", self.trajectory_path)
            return {}

    def _persist_trajectories_locked(self) -> None:
        if not self.trajectory_path:
            return
        try:
            self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.trajectory_path.with_suffix(self.trajectory_path.suffix + ".tmp")
            temp.write_text(json.dumps(self.trajectories, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp.replace(self.trajectory_path)
        except OSError:
            LOGGER.exception("保存轨迹失败: %s", self.trajectory_path)

    def _estop_locked(self) -> None:
        self._cancel_activity_locked()
        self._stop_locked()
        self.state = TeleopState.ESTOP
        self.armed = False
        self.last_command_at = None

    def _clear_estop_locked(self) -> None:
        if self.state == TeleopState.ESTOP:
            self.state = TeleopState.IDLE

    def _stop_locked(self) -> None:
        self.robot.stop()

    def _cancel_activity_locked(self) -> None:
        if self.mc_playback:
            self.mc_playback.stop()  # Intent only; never cancel a thread or an in-flight RPC.
        if self.recording_task:
            self.recording_task.cancel()
            self.recording_task = None
        if self.recording_teach_mode:
            end_teaching = getattr(self.robot, "end_upper_body_teaching", None)
            if end_teaching is not None:
                end_teaching()
        self.recording_name = None
        self.recording_frames = []
        self.recording_started_at = None
        self.recording_monotonic_started_at = None
        self.recording_teach_mode = False
        if self.playback_task:
            self.playback_task.cancel()
            self.playback_task = None
        self.playback_paused = False
        if not self.mc_playback and self._playback_busy():
            self.playback_state = "idle"
            self.playback_name = None

    def _snapshot_locked(self, now: float | None = None) -> BridgeSnapshot:
        now = time.monotonic() if now is None else now
        if self.mc_playback:
            self.playback_state = self.mc_playback.state
            self.playback_progress_ms = int(self.mc_playback.progress_ms)
            self.playback_duration_ms = int(self.mc_playback.duration_ms)
            self.playback_error = self.mc_playback.error
        age = None if self.last_command_at is None else max(0, int((now - self.last_command_at) * 1000))
        return BridgeSnapshot(
            self.state,
            self.armed,
            self.source,
            age,
            self.robot_connected,
            self.recording_name,
            len(self.recording_frames),
            self.recording_sample_rate_hz if self.recording_name else None,
            self.playback_state,
            self.playback_name,
            self.playback_progress_ms,
            self.playback_duration_ms,
            self.playback_error,
            self.control_capabilities(),
            self.recording_error,
            self.last_hand_command,
        )

    @staticmethod
    def _finite_number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise BridgeError("invalid_velocity", f"{name} 必须是有限数字")
        return float(value)
