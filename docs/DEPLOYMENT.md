# 部署说明

## 本地

```powershell
uv sync --group dev
uv run pytest
uv run python demo/teleop_demo.py --robot mock
uv run x2-ps5-input-test
```

## 机器人侧

机器人使用 ROS 2 Humble。本次源码已部署到 PC2（`10.0.1.41`）的
`/agibot/data/home/agi/x2_ps5_teleop`。以官方 `run` 用户启动，确保
AimDK 消息包和输入设备权限可用：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
python3 demo/teleop_demo.py --robot x2
```

从 `agi` 登录会话执行时，在上述命令外包一层
`sudo -u run bash -lc '...'`；不要在 PC1（`10.0.1.40`）运行。

运行前检查。DualSense 必须作为 PC2 的 Linux HID/SDL 设备出现；如果它仍连接到 X2
原生蓝牙或官方遥操链路，pygame 不会看到它。建议先用 USB 线直连 PC2 完成输入验证：

```bash
ls -l /dev/input
cat /proc/bus/input/devices
systemctl is-active bluetooth
python3 -m x2_ps5_teleop.dualsense_test
ros2 topic echo /joy
ros2 topic list | grep /aima/hal/joint
ros2 topic hz /aima/hal/joint/hand/state
```

诊断程序会输出 `pygame joysticks=N` 以及 SDL 设备名。`N=0` 时先暂停官方遥操连接，
将 DualSense 重新配对到 PC2，或使用 USB 线复测；`XDG_RUNTIME_DIR` 和 ALSA 警告通常不影响
HID 输入。只有诊断输出 `connected` 后，`STATE=DISCONNECTED` 才会消失。

`demo/teleop_demo.py --robot x2` 直接使用官方 AimDK 接口：发布 `/aima/mc/locomotion/velocity` (`aimdk_msgs/msg/McLocomotionVelocity`)，调用 `/aimdk_5Fmsgs/srv/SetMcAction`、`/aimdk_5Fmsgs/srv/SetMcPresetMotion` 并注册 `/aimdk_5Fmsgs/srv/SetMcInputSource`。具体服务是否可用以现场固件为准。

X2 Ultra 的开发计算单元是 PC2（`10.0.1.41`）；官方明确禁止在运控计算单元 PC1（`10.0.1.40`）运行二开程序。推荐在 `10.0.1.41` 或同网的外部上位机运行适配节点；先验证移动，再验证单个手部动作。配置实际模式服务、输入源名称和四个预设动作 ID。所有服务都要有超时重试和失败回退。

## 本次真机验证

- `GetHandType` 返回左右手 `value=1`，四个预设服务均可达。
- 测试前先发布零速度；随后按 RT/LT/R1/L1 顺序调用：右手挥手
  `(1002, area=2)`、左手挥手 `(1002, area=1)`、右手举手
  `(1001, area=2)`、左手举手 `(1001, area=1)`。
- 服务返回任务号 `27/28/29/30`，状态均为 `RUNNING (400)`；测试结束再次发布零速度。
- 随后 `GetMcAction` 为 `STAND_DEFAULT`、状态 `100`，没有残留动作。
- PC2 当时未连接 DualSense，输入诊断报告 `JOYSTICKS 0`；这通常表示手柄连接在 X2
  原生蓝牙/官方遥操链路，而不是 Demo 所在的 PC2。完成 PC2 的 USB 或蓝牙 HID 配对并在
  诊断中看到 `connected` 后，再运行上面的 Demo 验证按键链路。
- 当前四个 `SetMcPresetMotion` 验证动作是右/左手挥手和举手，属于上肢动作，不是灵巧手
  手指控制。`HandCommandArray` 的真实关节顺序、单位和限位尚未确认，不能直接用猜测的
  数值实现张手、握拳或抓取。
