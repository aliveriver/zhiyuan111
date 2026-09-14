# 部署说明

## 本地

```powershell
uv sync
uv run pytest
uv run python demo/teleop_demo.py --robot mock
uv run x2-ps5-input-test
```

## 机器人侧

机器人使用 ROS 2 Humble。将源码部署到 PC2（`10.0.1.41`）的
`/agibot/data/home/agi/x2_ps5_teleop`，并以官方 `run` 用户启动，确保
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

## 三节点只读调查

分别 SSH 登录三个 IP，并在每台机器上加载与运行程序相同的环境后执行：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH="$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH"
export AMENT_PREFIX_PATH="/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH"
export LD_LIBRARY_PATH="/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH"
python3 -m x2_ps5_teleop.inspect_node > "x2-node-$(hostname).txt"
```

调查命令只读取系统、进程、输入设备和 ROS 2 图，不发布 Topic，也不调用 Service/Action。
报告会展开与 `aima`、`aimdk`、`hand`、`joint`、`locomotion`、`joy` 和 `teleop`
相关的 Topic 类型及发布/订阅端点。由报告确定节点职责和灵巧手消息定义，不按 IP 猜测。

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

`demo/teleop_demo.py --robot x2` 直接使用官方 AimDK 接口：发布 `/aima/mc/locomotion/velocity` (`aimdk_msgs/msg/McLocomotionVelocity`)，调用 `/aimdk_5Fmsgs/srv/SetMcAction`、`/aimdk_5Fmsgs/srv/GetHandType` 并注册 `/aimdk_5Fmsgs/srv/SetMcInputSource`，手部使用 `/aima/hal/joint/hand/command` (`aimdk_msgs/msg/HandCommandArray`)。具体服务是否可用以现场固件为准。

X2 Ultra 的开发计算单元是 PC2（`10.0.1.41`）；官方明确禁止在运控计算单元 PC1（`10.0.1.40`）运行二开程序。推荐在 `10.0.1.41` 或同网的外部上位机运行适配节点；先验证移动，再验证单个手部动作。配置实际模式服务、输入源名称和四个预设动作 ID。所有服务都要有超时重试和失败回退。

## 仓库原有真机验证记录（部署前需复核）

- `GetHandType` 返回左右手 `value=1`；当前程序使用 `/aima/hal/joint/hand/command` 发布灵巧手命令。
- 测试前先发布零速度；随后按 RT/LT/R1/L1 顺序调用：右手挥手
  `(1002, area=2)`、左手挥手 `(1002, area=1)`、右手举手
  `(1001, area=2)`、左手举手 `(1001, area=1)`。
- 服务返回任务号 `27/28/29/30`，状态均为 `RUNNING (400)`；测试结束再次发布零速度。
- 随后 `GetMcAction` 为 `STAND_DEFAULT`、状态 `100`，没有残留动作。
- PC2 当时未连接 DualSense，输入诊断报告 `JOYSTICKS 0`；这通常表示手柄连接在 X2
  原生蓝牙/官方遥操链路，而不是 Demo 所在的 PC2。完成 PC2 的 USB 或蓝牙 HID 配对并在
  诊断中看到 `connected` 后，再运行上面的 Demo 验证按键链路。
- OmniHand `HandCommandArray` 的官方字段和 10 槽结构已确认；具体位置值仍需根据现场状态
  反馈和安全范围标定，不能将示例位置直接视为抓取极限。
