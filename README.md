# AgiBot X2 + PS5 DualSense 遥操作演示

本仓库包含首版低速演示闭环：

```text
DualSense 蓝牙 -> 输入读取器 -> IDLE/TELEOP 状态机 -> Mock 或 AimDK ROS 2
```

默认状态为 `IDLE`。按 `OPTIONS` 切换 `TELEOP`；按 `PS` 键触发锁存的软件急停。手柄断连或输入超过 0.5 秒未更新时会发送零速度。物理急停始终是必须保留的安全措施。

## 安装与测试

```powershell
uv sync --group dev
uv run pytest
```

## Mock 演示

```powershell
uv run python demo/teleop_demo.py --robot mock
```

输入六个轴值，再输入按钮值。轴依次为 `LX LY RX RY LT RT`；按钮使用 SDL 编号（`Cross=0`、`Circle=1`、`Square=2`、`Triangle=3`、`L1=4`、`R1=5`、`OPTIONS=9`、`PS=12`）。可用命令为 `teleop`、`idle`、`estop`、`disconnect`、`clear` 和 `quit`。

```text
teleop
0 -1 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 1
```

Mock 会打印 `STATE=...`、`MOVE vx=... vy=... wz=...`、`MODE=...` 和 `HAND ACTION=...`。

## DualSense 输入诊断

先在操作系统或 X2 蓝牙设置中配对手柄，然后运行：

```powershell
uv run x2-ps5-input-test
```

程序会持续打印 `LX`、`LY`、`RX`、`RY`、`LT`、`RT`、`L1`、`R1`、`Cross`、`Circle`、`Square` 和 `Triangle`，以及 `connected`/`disconnected` 状态变化。SDL 编号可能随固件变化，修改 `config/controller.yaml` 前请先核对此输出。

## 映射

| 输入 | 功能 |
|---|---|
| 左摇杆 Y/X | 前后、横移 |
| 右摇杆 X | 转向 |
| OPTIONS | 切换 IDLE / TELEOP |
| PS | 软件急停 |
| × / ○ / △ / □ | PASSIVE / DAMPING / JOINT / STAND |
| RT / LT / R1 / L1 | 四个动作事件（当前配置为上肢验证动作） |

实际 SDL 编号需先运行输入诊断确认，之后调整 `config/controller.yaml`。

## X2 ROS 2 模式

```bash
# 在 PC2（10.0.1.41）上以官方 `run` 服务用户运行。`run` 用户拥有
# AimDK 运行时，并具有机器人输入设备的访问权限。
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
python3 demo/teleop_demo.py --robot x2
```

从 `agi` SSH 会话执行时，请在上述命令外包一层
`sudo -u run bash -lc '...'`。AimDK 消息包安装在官方 `common` 和 `ec`
工作空间中；仅加载 `/opt/ros/humble/setup.bash` 不足以运行本项目。
切换到 `TELEOP` 前，必须释放物理急停并确认机器人处于稳定状态。

适配层使用 AimDK X2 1.0.0 文档中确认的接口：`/aima/mc/locomotion/velocity`
（`aimdk_msgs/msg/McLocomotionVelocity`）、`/aimdk_5Fmsgs/srv/SetMcAction`、
`/aimdk_5Fmsgs/srv/SetMcPresetMotion` 和 `/aimdk_5Fmsgs/srv/SetMcInputSource`。
请在开发计算机（`10.0.1.41`）或外部有线主机上运行，绝不要在 PC1（`10.0.1.40`）上运行。

当前配置的官方预设动作实际是上肢验证动作：RT 右手挥手 `(motion=1002, area=2)`、LT
左手挥手 `(1002, 1)`、R1 右手举手 `(1001, 2)`、L1 左手举手 `(1001, 1)`。这些动作
不是灵巧手手指姿态，不能作为“张手、握拳、抓取、释放”的实现。灵巧手接口
`/aima/hal/joint/hand/command` 的关节顺序、单位和限位仍需在现场确认，确认前不要发送猜测的
关节数组。任何真机测试前都要确认服务可用且机器人状态正常；物理急停始终是必须保留的安全措施。

## 排查记录

已检查节点均为 Ubuntu 22.04.5 / ROS 2 Humble：`10.0.1.40` 是运行
`mc_app_main`、EtherCAT 和原生 `aima-rc-app` 的运控 PC1；`10.0.1.41` 是运行
AimDK 控制图的开发计算机；`10.0.1.42` 是感知/交互计算机。出厂遥控器通过机器人
蓝牙设置配对，并使用 PS 徽标键唤醒。文档记录的启动门限为前进 0.09 m/s、横移
0.60 m/s、偏航 0.03 rad/s（取决于固件），因此本演示将速度上限保持为 `0.12`、
`0.08` 和 `0.15`。

参考资料：[AimDK X2 文档](https://x2-aimdk.agibot.com/zh-cn/latest/about_agibot_X2/index.html)、[X2 用户指南](https://www.agibot.com.cn/filepage/291.html)。

## 故障排查

- 没有检测到手柄：先用 USB 线将 DualSense 连接到 PC2 验证输入链路，再按 PS 键并重新运行
  `uv run x2-ps5-input-test`。诊断会打印 `pygame joysticks=N` 和设备名；如果为 `0`，检查
  `ls -l /dev/input`、`cat /proc/bus/input/devices`，以及 `systemctl is-active bluetooth`。
  若手柄已连接到 X2 原生蓝牙或官方遥操链路，它不会作为 PC2 的 Linux HID 设备出现，请先
  暂停官方遥操连接或改为配对到 PC2。
- 按钮或轴错误：查看诊断输出，并调整 `config/controller.yaml`。
- ROS 导入错误：以 `run` 用户运行，加载 `/agibot/software/cobridge/setup.bash`，并导出上文所示的 `common`/`ec` Python 和库路径。
- 服务不可用：在 PC2 或外部有线主机上检查 `ros2 service list`。
- 机器人不移动：确认 `STATE=TELEOP`、物理急停已释放、机器人处于稳定状态，并确认 MC 仲裁接受 `ps5_demo`。
