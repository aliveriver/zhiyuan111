import asyncio
import io
import json

import websockets

from x2_ps5_teleop.bridge.controller import BridgeController
from x2_ps5_teleop.bridge.server import TeleopBridgeServer
from x2_ps5_teleop.robot.motion import MockRobot


def test_app_protocol_records_and_replays_with_position_presets(tmp_path):
    async def scenario():
        robot = MockRobot(io.StringIO())
        controller = BridgeController(robot, trajectory_path=tmp_path / "trajectories.json",
                                      hand_pose_path=tmp_path / "hand_poses.json")
        bridge = TeleopBridgeServer(controller)
        async with websockets.serve(bridge.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as socket:
                await socket.send(json.dumps({"type": "hello", "protocol_version": 1, "client_id": "app-test"}))
                hello = json.loads(await socket.recv())
                assert hello["control_capabilities"]["backend"] == "mock"
                sequence = 0

                async def command(payload):
                    nonlocal sequence
                    sequence += 1
                    await socket.send(json.dumps({**payload, "sequence": sequence}))
                    while True:
                        message = json.loads(await asyncio.wait_for(socket.recv(), 1))
                        assert message["type"] != "error", message
                        if message["type"] == "ack" and message["sequence"] == sequence:
                            return message["state"]

                await command({"type": "arm", "enabled": True})
                await command({"type": "hand_pose_save", "name": "抓握", "side": "right", "positions": [0.2] * 10})
                await command({"type": "hand_pose_apply", "name": "抓握"})
                await command({"type": "trajectory_record_start", "name": "单手抓取", "sample_rate_hz": 20})
                saved = await command({"type": "trajectory_record_stop"})
                assert saved["trajectories"][0]["right_hand_joints"] == 10
                for _ in range(2):
                    await command({"type": "trajectory_play", "name": "单手抓取", "speed": 0.25})
                    if controller.playback_task:
                        await controller.playback_task
                    assert controller.playback_state == "completed"
                feedback = await command({"type": "hand_state"})
                assert feedback["hand_feedback"]["right"] == [0.2] * 10
                assert feedback["hand_feedback"]["left"] == [0.0] * 10
        await controller.close()
    asyncio.run(scenario())
