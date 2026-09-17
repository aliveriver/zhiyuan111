"""PC2 上运行的手机 WebSocket 桥接服务。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import secrets
import sys
from typing import Any
from websockets.exceptions import ConnectionClosed

from .controller import BridgeController, BridgeError
from ..robot.motion import MockRobot, X2RosRobot
from ..robot.mc_playback import MCPlayback, load_profile, runtime_test_profile
from ..settings import DEFAULT_CONFIG_PATH, load_settings

PROTOCOL_VERSION = 1
LOGGER = logging.getLogger(__name__)


def resolve_playback_profile(robot: str, profile_path: Path | None) -> dict[str, Any] | None:
    if robot == "mock":
        if profile_path:
            raise ValueError("Mock 不加载实机 MC 验收配置")
        return None
    return load_profile(profile_path) if profile_path else runtime_test_profile()


class TeleopBridgeServer:
    def __init__(self, controller: BridgeController):
        self.controller = controller
        self.channels: dict[str, Any] = {}
        self.send_locks: dict[str, asyncio.Lock] = {}
        self.broadcast_lock = asyncio.Lock()

    async def handler(self, websocket) -> None:
        session_id = secrets.token_urlsafe(18)
        LOGGER.info("WebSocket 新连接 session=%s remote=%s", session_id, getattr(websocket, "remote_address", "-"))
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            hello = self._decode(raw)
            if hello.get("type") != "hello" or hello.get("protocol_version") != PROTOCOL_VERSION:
                LOGGER.warning("拒绝 hello session=%s payload=%s", session_id, hello)
                raise BridgeError("invalid_hello", "首帧必须是 protocol_version=1 的 hello")
            client_id = hello.get("client_id")
            old_session = await self.controller.register(session_id, client_id)
            self.channels[session_id] = websocket
            self.send_locks[session_id] = asyncio.Lock()
            if old_session is not None and old_session in self.channels:
                await self._send_error(old_session, "replaced", "同一设备建立了新连接")
                await self.channels[old_session].close(code=4001, reason="replaced")
            await self._send(session_id, {
                "type": "hello_ack",
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": ["velocity", "mode", "trajectory", "hand_target", "hand_positions", "hand_pose_library", "state_recording"],
                "control_capabilities": self.controller.control_capabilities(),
            })
            await self._broadcast()
            async for raw in websocket:
                try:
                    message = self._decode(raw)
                    state = await self.controller.handle(session_id, message)
                    await self._send(session_id, {"type": "ack", "sequence": message.get("sequence"), "request_type": message.get("type"), "state": state})
                    await self._broadcast()
                except BridgeError as exc:
                    LOGGER.warning("控制消息失败 session=%s code=%s message=%s", session_id, exc.code, exc.message)
                    await self._send_error(session_id, exc.code, exc.message)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    LOGGER.exception("解析控制消息失败 session=%s", session_id)
                    await self._send_error(session_id, "invalid_message", str(exc))
                except ConnectionClosed:
                    break
        except BridgeError as exc:
            await self._send_raw(websocket, {"type": "error", "code": exc.code, "message": exc.message})
            await websocket.close(code=1008, reason=exc.code)
        except (asyncio.TimeoutError, json.JSONDecodeError, TypeError, ValueError):
            await websocket.close(code=1008, reason="invalid hello")
        finally:
            LOGGER.info("WebSocket 关闭 session=%s", session_id)
            self.channels.pop(session_id, None)
            self.send_locks.pop(session_id, None)
            if await self.controller.disconnect(session_id):
                await self._broadcast()

    async def watchdog_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(0.05)
            changed = await self.controller.watchdog()
            tick += 1
            if changed or tick % 4 == 0:
                await self._broadcast()

    async def _broadcast(self) -> None:
        state = (await self.controller.snapshot()).as_dict()
        for session_id in list(self.channels):
            try:
                await self._send(session_id, state)
            except Exception:
                pass

    async def _send_error(self, session_id: str, code: str, message: str) -> None:
        if session_id in self.channels:
            await self._send(session_id, {"type": "error", "code": code, "message": message})

    async def _send(self, session_id: str, payload: dict[str, Any]) -> None:
        websocket = self.channels.get(session_id)
        if websocket is None:
            return
        async with self.send_locks[session_id]:
            await websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _send_raw(self, websocket, payload: dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            pass

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise TypeError("WebSocket 消息必须是 JSON 对象")
        return message


async def serve(args) -> None:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - 安装依赖后由入口执行
        raise RuntimeError("桥接服务需要 websockets 依赖，请先运行 uv sync") from exc

    profile_path = getattr(args, "mc_commissioning_profile", None)
    interlock_path = args.data_dir / "mc-motion-unconfirmed.lock"
    profile = resolve_playback_profile(args.robot, profile_path)
    settings = load_settings(args.config)
    LOGGER.info("桥接启动 protocol=%s python=%s module=%s", PROTOCOL_VERSION, sys.executable, __file__)
    if args.robot == "mock":
        robot = MockRobot(__import__("sys").stdout)
    else:
        robot = X2RosRobot(source=args.source, preset_actions=settings.presets)
    controller = BridgeController(
        robot,
        source=args.source,
        timeout=args.timeout,
        velocity_limits=(
            settings.mapping.max_linear_x,
            settings.mapping.max_linear_y,
            settings.mapping.max_angular_z,
        ),
        trajectory_path=args.data_dir / "trajectories.json",
        hand_pose_path=args.data_dir / "hand_poses.json",
        mc_playback=MCPlayback(profile, interlock_path=interlock_path) if profile else None,
    )
    server = TeleopBridgeServer(controller)
    watchdog = asyncio.create_task(server.watchdog_loop())
    try:
        async with websockets.serve(server.handler, args.host, args.port, max_size=16 * 1024, ping_interval=20):
            LOGGER.info("WebSocket bridge listening on ws://%s:%s robot=%s source=%s", args.host, args.port, args.robot, args.source)
            await asyncio.Future()
    finally:
        watchdog.cancel()
        await controller.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    parser = argparse.ArgumentParser(description="AgiBot X2 手机 WebSocket 遥操作桥接服务")
    parser.add_argument("--robot", choices=("mock", "x2"), default="mock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=0.4)
    parser.add_argument("--source", default="mobile_app")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".x2_ps5_teleop",
                        help="轨迹和手部预设目录；Mock 联调应使用独立目录")
    parser.add_argument("--mc-commissioning-profile", type=Path,
                        help="可选的已审阅 MC 现场验收 JSON；省略时进入有人值守测试模式")
    args = parser.parse_args()
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
