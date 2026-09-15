# AgiBot X2 + PS5 DualSense 遥操作演示

本仓库包含首版低速演示闭环：

```text
DualSense 蓝牙 -> 输入读取器 -> IDLE/TELEOP 状态机 -> Mock 或 AimDK ROS 2
```

默认状态为 `IDLE`。按 `OPTIONS` 切换 `TELEOP`；按 `PS` 键触发锁存的软件急停。手柄断连或输入超过 0.5 秒未更新时会发送零速度。物理急停始终是必须保留的安全措施。

## 安装与测试

```powershell
uv sync
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

程序会持续打印 `LX`、`LY`、`RX`、`RY`、`LT`、`RT`、`L1`、`R1`、`Cross`、`Circle`、`Square` 和 `Triangle`，以及 `connected`/`disconnected` 状态变化。SDL 编号可能随固件变化，修改 `config/controller.json` 前请先核对此输出。

## 映射

| 输入 | 功能 |
|---|---|
| 左摇杆 Y/X | 前后、横移 |
| 右摇杆 X | 转向 |
| OPTIONS | 切换 IDLE / TELEOP |
| PS | 软件急停 |
| × / ○ / △ / □ | PASSIVE / DAMPING / JOINT / STAND |
| RT / LT / R1 / L1 | 四个动作事件（当前配置为上肢验证动作） |

实际 SDL 编号需先运行输入诊断确认，之后调整 `config/controller.json`。
也可以通过 `--config /path/to/controller.json` 使用另一份配置。

## X2 ROS 2 模式

## 手机 Expo App

手机端代码位于 [mobile](mobile)，PC2 桥接服务位于 `src/x2_ps5_teleop/bridge`。
完整流程见 [手机 App 使用说明](docs/MOBILE_APP_PLAN.md#手机-app-使用流程)。

### 本地 Mock 联调

```powershell
uv sync
uv run pytest
./scripts/start_mobile_bridge.sh --robot mock --host 0.0.0.0 --port 8765
cd mobile
npm install
npm start
```

手机和运行桥接服务的电脑连接同一个局域网，在 App 地址栏填写
`ws://电脑局域网IP:8765`，点击“连接”。Mock 模式只打印命令，不会连接机器人，适合
先验证连接、解锁、摇杆、急停和断连停车。

### 真机使用前提

真机模式只能在 PC2（通常为 `10.0.1.41`）运行，不能在 PC1（`10.0.1.40`）运行。必须
以官方 `run` 用户加载 AimDK 环境后启动：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
./scripts/start_mobile_bridge.sh --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app
```

启动后 App 初始为 `IDLE`。确认物理急停已释放、周围无人且机器人状态稳定，再点击
“进入 TELEOP”。松开摇杆、退出 App、切后台、断开网络或超过 0.4 秒未收到控制帧时，
桥接服务都会发送零速度。物理急停始终必须有人值守。

### 手机控制规则

- 同一时间只允许一个手机控制；另一台手机会收到 `busy`。
- 同一手机重新连接时，新连接会接管旧连接，旧连接的延迟消息不会继续控制机器人。
- 速度帧由服务端再次限幅，服务端拒绝乱序或重复序列号。
- 手部预设动作会先停车，执行后回到 `IDLE`，需要再次点击“进入 TELEOP”。
- “急停”是锁存状态，必须在确认安全后发送清除急停，再重新进入 TELEOP。

### 关于官方蓝牙遥控器连接

如果按 X2 官方设置将 DualSense 直接与机器人配对，手柄会出现在运控计算机
PC1（`10.0.1.40`）的 Linux 输入设备中（例如 `DualSense Wireless Controller` / `js0`）。
官方 `soc0_rc` 节点会在 PC1 内部读取这些事件并直接发布底盘速度；现场 ROS 图没有
`/joy` 或公开的 PS5 按键 Topic。因此部署在 PC2 的本程序不能从这条原生蓝牙链路读取
摇杆和按键，`pygame joysticks=0` 在这种情况下是预期结果。

本程序有两种互斥的输入方案：

1. 将手柄作为 Linux HID 配对到运行本程序的 PC2，由 `pygame` 读取，再向 ROS 发布官方
   AimDK 速度/手部命令。
2. 保持官方“手柄连接机器人”方式，但需要 AgiBot 提供 `soc0_rc` 的公开输入转发
   Topic/API；当前图中尚未发现该接口，不能凭经验猜测或在 PC1 运行二开程序代替它。

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
`/aimdk_5Fmsgs/srv/GetHandType`、`/aimdk_5Fmsgs/srv/SetMcInputSource` 和
`/aima/hal/joint/hand/command`（`aimdk_msgs/msg/HandCommandArray`）。
请在开发计算机（`10.0.1.41`）或外部有线主机上运行，绝不要在 PC1（`10.0.1.40`）上运行。

RT/LT/R1/L1 触发配置化的 OmniHand `HandCommandArray` 姿态；每只手发布 10 个带官方名称
和运动参数的命令槽。位置值需要在现场根据手部状态反馈和安全范围标定。预设动作触发后程序
会退回 `IDLE`，需要再次按 `OPTIONS` 才能恢复移动。
任何真机测试前都要确认服务可用且机器人状态正常；物理急停始终是必须保留的安全措施。

## 仓库原有排查记录（部署前需复核）

仓库原有记录显示节点均为 Ubuntu 22.04.5 / ROS 2 Humble：`10.0.1.40` 是运行
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
- 按钮或轴错误：查看诊断输出，并调整 `config/controller.json`。
- ROS 导入错误：以 `run` 用户运行，加载 `/agibot/software/cobridge/setup.bash`，并导出上文所示的 `common`/`ec` Python 和库路径。
- 服务不可用：在 PC2 或外部有线主机上检查 `ros2 service list`。
- 机器人不移动：确认 `STATE=TELEOP`、物理急停已释放、机器人处于稳定状态，并确认 MC 仲裁接受 `ps5_demo`。
