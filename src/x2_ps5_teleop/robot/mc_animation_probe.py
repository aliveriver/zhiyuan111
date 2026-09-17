"""Prepare or run an attended, single-joint MC-animation commissioning test.

Default operation is read-only ROS sampling plus local CSV generation. No
robot node from motion.py is constructed and no MC input source is registered.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from .mc_animation import ARM_NAMES, WAIST_NAMES, compile_animation, hold_animation, positions, waist_positions


class Probe:
    def __init__(self):
        import rclpy
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        from aimdk_msgs.msg import JointStateArray, JointCommandArray, HandStateArray, McCommonState
        from aimdk_msgs.srv import GetSystemState, SetMcPresetMotion
        self.ros = rclpy
        self.SetMotion = SetMcPresetMotion
        self.GetSystem = GetSystemState
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        self.context = Context()
        rclpy.init(context=self.context)
        self.node = rclpy.create_node("x2_mc_animation_commissioning", context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.latest = {}
        self.received = {}
        self.counts = {}
        self.trace = []
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        topics = [
            ("arm", JointStateArray, "/aima/hal/joint/arm/state"),
            ("hand", HandStateArray, "/aima/hal/joint/hand/state"),
            ("mc", McCommonState, "/aima/mc/common/state"),
            *((part + "_command", JointCommandArray, f"/aima/hal/joint/{part}/command")
              for part in ("arm", "leg", "waist", "head")),
        ]
        for key, kind, topic in topics:
            self.node.create_subscription(kind, topic, lambda msg, key=key: self.receive(key, msg), qos)
        self.system = self.node.create_client(GetSystemState, "/aimdk_5Fmsgs/srv/GetSystemState")
        self.motion = self.node.create_client(SetMcPresetMotion, "/aimdk_5Fmsgs/srv/SetMcPresetMotion")
        self._player_state_names = {0: "IDLE", 1: "PRE_PLAYING", 2: "PLAYING", 3: "INTERRUPTING", 4: "ERROR"}

    def receive(self, key, msg):
        self.latest[key] = msg
        self.received[key] = time.monotonic()
        self.counts[key] = self.counts.get(key, 0) + 1
        if key == "mc" and getattr(self, "mc_observer", None):
            self.mc_observer(msg)

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.executor.spin_once(timeout_sec=min(0.01, max(0, end - time.monotonic())))

    def sample(self):
        row = {"monotonic": time.monotonic()}
        for key in ("arm", "arm_command", "leg_command", "waist_command", "head_command"):
            msg = self.latest.get(key)
            row[key] = {j.name: float(j.position) for j in msg.joints} if msg else {}
            row[key + "_age"] = row["monotonic"] - self.received.get(key, 0)
        mc = self.latest.get('mc')
        row['mc'] = ({'action': mc.action_info.action_desc,
                      'action_status': mc.action_info.status.value,
                      'player_state': mc.motion_status.player_state.value,
                      'player_state_name': self._player_state_names.get(mc.motion_status.player_state.value, 'UNKNOWN'),
                      'control_area': mc.motion_status.control_area.value} if mc else None)
        row['mc_age'] = row['monotonic'] - self.received.get('mc', 0)
        hand = self.latest.get('hand')
        row['hand'] = ({side: [float(j.position) for j in getattr(hand, side + '_hands')]
                        for side in ('left', 'right')} if hand else None)
        row['hand_age'] = row['monotonic'] - self.received.get('hand', 0)
        self.trace.append(row)
        return row

    def frame(self, *, include_waist=False):
        for key in ("arm", "hand"):
            if time.monotonic() - self.received.get(key, 0) > 0.25:
                raise RuntimeError(f"{key} 状态缺失或超过 250 ms")
        def joints(values):
            return [{"name": j.name, "position": float(j.position)} for j in values]
        result = {"type": "upper_body", "t_ms": 0,
                  "arm": joints(self.latest["arm"].joints),
                  "left_hand": joints(self.latest["hand"].left_hands),
                  "right_hand": joints(self.latest["hand"].right_hands)}
        if include_waist:
            if ("waist_command" not in self.latest
                    or time.monotonic() - self.received.get("waist_command", 0) > 0.25):
                raise RuntimeError("waist 目标缺失或超过 250 ms")
            result["waist"] = joints(self.latest["waist_command"].joints)
            waist_positions(result)
        positions(result)
        return result

    def call(self, client, request):
        if not client.wait_for_service(timeout_sec=1):
            raise RuntimeError("所需 MC 服务不可用")
        future = client.call_async(request)
        self.executor.spin_until_future_complete(future, timeout_sec=2)
        if not future.done():
            raise RuntimeError("MC 服务响应超时，不能据此认为机器人未执行")
        return future.result()

    def inspect(self):
        self.wait(1)  # Allow DDS discovery before the first service request.
        result = self.call(self.system, self.GetSystem.Request())
        self.wait(1)
        mc = self.latest.get("mc")
        return {"system_state": result.cur_state,
                "action": mc.action_info.action_desc if mc else None,
                "action_status": mc.action_info.status.value if mc else None,
                "player_state": mc.motion_status.player_state.value if mc else None,
                "counts": dict(self.counts)}

    def require_standing(self):
        from aimdk_msgs.msg import McActionStatus, McPlayerState
        status = self.inspect()
        if (status["system_state"] != "Business" or status["action"] != "STAND_DEFAULT"
                or status["action_status"] != McActionStatus.RUNNING
                or status["player_state"] != McPlayerState.IDLE):
            raise RuntimeError(f"需由现场人员先准备稳定站立且无其他动画；当前 {status}")
        if time.monotonic() - self.received.get("mc", 0) > 0.5:
            raise RuntimeError("MC 状态已过期")

    def play(self, path, interrupt=False):
        from aimdk_msgs.msg import McControlArea, McPresetMotion
        request = self.SetMotion.Request()
        request.header.stamp = self.node.get_clock().now().to_msg()
        # This combination is present in this robot's ani_table. ani_path is
        # mandatory here, so no built-in wave resource should be selected.
        request.area.value = McControlArea.LEFT_HAND | McControlArea.RIGHT_HAND
        request.motion.value = McPresetMotion.WAVE_HAND
        request.ani_path = path
        request.interrupt = interrupt
        response = self.call(self.motion, request).response
        code = int(response.header.code)
        state = int(response.state.value)
        # AimDK ResponseHeader uses code=0 for a successful request.  The
        # CommonState value is separate: 400 means the task is RUNNING.
        if code != 0:
            raise RuntimeError(f"MC 拒绝动画（未确认执行）：code={code}, state={state}")
        return {"task_id": response.task_id, "state": response.state.value}

    def close(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.context.shutdown()


def upload(content: bytes, host: str, control_path: str | None, remote_path: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_.@:-]+", host) or host.startswith("-"):
        raise ValueError("SSH 主机格式无效")
    digest = hashlib.sha256(content).hexdigest()
    # File creation only. Never shell-expand trajectory names or file data.
    code = ("from pathlib import Path; import sys,hashlib; "
            f"p=Path({remote_path!r}); p.parent.mkdir(parents=True,exist_ok=True); "
            "b=sys.stdin.buffer.read(); "
            f"assert hashlib.sha256(b).hexdigest()=={digest!r}; "
            "p.write_bytes(b); print(hashlib.sha256(p.read_bytes()).hexdigest())")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2"]
    if control_path:
        command += ["-S", control_path]
    command += [host, "python3 -c " + shlex.quote(code)]
    try:
        result = subprocess.run(command, input=content, capture_output=True, timeout=3, check=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip() or f"退出码 {exc.returncode}"
        raise RuntimeError(f"上传动画到 {host} 失败：{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"上传动画到 {host} 超时；请检查 SSH 连通性和认证") from exc
    if result.stdout.decode().strip() != digest:
        raise RuntimeError("soc0 动画文件哈希校验失败")


def single_joint_frames(measured, joint, delta, duration_ms=3000):
    if joint not in ARM_NAMES or not 0 < abs(delta) <= 0.02:
        raise ValueError("首轮只允许已知手臂单关节，位移最多 0.02 rad")
    if (isinstance(duration_ms, bool) or not isinstance(duration_ms, int)
            or not 3000 <= duration_ms <= 60000 or duration_ms % 50):
        raise ValueError("动画时长必须为 3000～60000 ms 且为 50 ms 的整数倍")
    frames = []
    sample_count = duration_ms // 50
    for tick in range(sample_count + 1):  # 20 Hz recorded-format input.
        frame = copy.deepcopy(measured)
        frame["t_ms"] = tick * 50
        for item in frame["arm"]:
            if item["name"] == joint:
                item["position"] += delta * tick / sample_count
        frames.append(frame)
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="新建的诊断目录，不使用轨迹数据目录")
    parser.add_argument("--joint", default="left_wrist_yaw_joint", choices=ARM_NAMES)
    parser.add_argument("--delta", type=float, default=0.02)
    parser.add_argument("--duration-ms", type=int, default=3000,
                        help="单关节轨迹时长，3000～60000 ms；考证一建议至少 10000")
    parser.add_argument("--include-waist", action="store_true",
                        help="考证二：加入新鲜恒定腰部目标列")
    parser.add_argument("--ssh-host", default="run@10.0.1.40")
    parser.add_argument("--ssh-control-path")
    parser.add_argument("--execute", action="store_true", help="实际发出单关节动画及静止替换请求")
    parser.add_argument("--attended-estop", action="store_true")
    parser.add_argument("--unloaded", action="store_true")
    args = parser.parse_args()
    if args.execute and not (args.attended_estop and args.unloaded):
        parser.error("实机测试必须确认物理急停有人值守且机器人空载")
    args.output.mkdir(parents=True, exist_ok=False)
    probe = Probe()
    attempted = False
    hold_sent = False
    report = {"executed": False, "commissioned": False}
    remote = "/tmp/x2-animation-probe/" + uuid.uuid4().hex
    try:
        report["initial"] = probe.inspect()
        measured = probe.frame(include_waist=args.include_waist) if args.include_waist else probe.frame()
        frames = single_joint_frames(measured, args.joint, args.delta, args.duration_ms)
        animation = compile_animation(frames, include_waist=args.include_waist)
        (args.output / "trajectory.json").write_text(json.dumps({"single_joint": frames}))
        (args.output / "probe.csv").write_bytes(animation.content)
        (args.output / "hold-preview.csv").write_bytes(hold_animation(measured).content)
        report.update(joint=args.joint, delta=args.delta, duration_ms=args.duration_ms,
                      include_waist=args.include_waist,
                      waist_channels=list(WAIST_NAMES) if args.include_waist else [],
                      scheme="waist_hold" if args.include_waist else "extended_duration",
                      csv_sha256=animation.sha256, source_rate_hz=20, csv_tick_ms=2,
                      csv_rows=animation.rows)
        if not args.execute:
            print(json.dumps(report, ensure_ascii=False))
            return
        probe.require_standing()
        # Rebuild after standing validation so preparation cannot reuse a pose
        # captured while the operator was changing modes.
        measured = probe.frame(include_waist=args.include_waist) if args.include_waist else probe.frame()
        frames = single_joint_frames(measured, args.joint, args.delta, args.duration_ms)
        animation = compile_animation(frames, include_waist=args.include_waist)
        (args.output / "trajectory.json").write_text(json.dumps({"single_joint": frames}))
        report["csv_sha256"] = animation.sha256
        (args.output / "probe.csv").write_bytes(animation.content)
        upload(animation.content, args.ssh_host, args.ssh_control_path, remote + "/probe.csv")
        probe.wait(0.1)
        current_frame = probe.frame(include_waist=args.include_waist) if args.include_waist else probe.frame()
        current = positions(current_frame)
        if max(abs(current[name] - value) for name, value in positions(measured).items()) > 0.01:
            raise RuntimeError("准备期间姿态发生变化，拒绝执行")
        attempted = True  # Includes timeout: MC may already have accepted it.
        report["start_response"] = probe.play(remote + "/probe.csv")
        report["executed"] = True
        for _ in range(40):
            probe.wait(0.05)
            probe.sample()
        # Mid-playback stop candidate: interrupt with freshly measured pose.
        frozen = probe.frame(include_waist=args.include_waist) if args.include_waist else probe.frame()
        hold = hold_animation(frozen)
        (args.output / "hold.csv").write_bytes(hold.content)
        report["stop_requested_at"] = time.monotonic()
        upload(hold.content, args.ssh_host, args.ssh_control_path, remote + "/hold.csv")
        report["hold_response"] = probe.play(remote + "/hold.csv", interrupt=True)
        hold_sent = True
        for _ in range(40):
            probe.wait(0.05)
            probe.sample()
        report["final"] = probe.inspect()
        # No automatic unlock: command/feedback traces need review, including
        # body output isolation and stop latency, before full replay is enabled.
        report["review_required"] = "检查 trace.json 中停止延迟、臂目标/反馈、腰头目标及腿部持续输出"
        print(json.dumps(report, ensure_ascii=False))
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        if attempted and not hold_sent:
            try:
                probe.wait(0.05)
                pose = probe.frame(include_waist=args.include_waist) if args.include_waist else probe.frame()
                hold = hold_animation(pose)
                upload(hold.content, args.ssh_host, args.ssh_control_path, remote + "/fallback-hold.csv")
                report["fallback_hold"] = probe.play(remote + "/fallback-hold.csv", interrupt=True)
            except Exception as exc:
                report["stop_error"] = f"无法确认停止，请现场使用物理急停：{exc}"
                print(report["stop_error"], flush=True)
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (args.output / "trace.json").write_text(json.dumps(probe.trace, ensure_ascii=False))
        probe.close()


if __name__ == "__main__":
    main()
