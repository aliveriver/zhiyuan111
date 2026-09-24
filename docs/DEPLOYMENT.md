# 部署说明

## 本地

```powershell
uv sync
uv run pytest
uv run python demo/teleop_demo.py --robot mock
uv run x2-ps5-input-test
```

## 手机 App 与 PC2 桥接

仓库提供了启动脚本 `scripts/start_mobile_bridge.sh`。脚本会自动判断后端：在
`10.0.1.41` 且当前用户为官方 `run` 时默认启动 `x2` 真机后端；其它主机默认使用
Mock。可以用 `--robot mock` 或 `--robot x2` 显式覆盖，脚本也会拒绝在已知 PC1 地址
`10.0.1.40` 上启动。

### 网络要求

手机和 PC2 必须位于同一局域网，且 Wi-Fi AP 允许客户端互访。桥接服务默认监听
`0.0.0.0:8765`；手机访问 PC2 的局域网地址，例如 `ws://10.0.1.41:8765`。不要把
8765 端口暴露到公网，也不要让手机直接连接 PC1 或 ROS 2。

首次部署建议先运行 Mock 后端：

```bash
cd /path/to/x2_ps5_teleop
uv sync
uv run pytest
chmod +x scripts/start_mobile_bridge.sh
./scripts/start_mobile_bridge.sh --robot mock
```

电脑端另开终端启动 Expo：

```powershell
cd mobile
npm install
npm run typecheck
npm start
```

手机用 Expo Go、Development Build 或 EAS APK 打开项目。手机与 PC2 连接同一允许设备互访的 Wi-Fi，在 App 地址栏留空并点击“自动发现并连接”；App 会按手机当前 IPv4 扫描同一 `/24` 子网的 `8765` 端口并验证桥接协议。也可填写
`ws://<PC2局域网IP>:8765` 后手动连接。先验证 App 的连接、解锁、摇杆、急停和断连停车，确认
Mock 终端只打印预期命令后，才能切换真机后端。

启动后日志应包含 `桥接源码: .../src/x2_ps5_teleop/bridge/server.py`。脚本会先按本项目
PID/命令行停止旧桥接，再启动新进程，不要直接运行系统中旧的 `x2-teleop-bridge` 可执行文件。

### 真机桥接启动

真机桥接必须运行在 PC2（`10.0.1.41`）或已确认能访问 AimDK ROS 图的外部上位机，
禁止在 PC1（`10.0.1.40`）运行。以官方 `run` 用户加载环境：

SSH 登录 PC2 后，直接执行下面的命令即可启动真机后端；脚本会自行加载 `cobridge` 和
AimDK 环境，不需要手工 `source` 或导出环境变量：

```bash
cd ~/zhiyuan111-main
./scripts/start_mobile_bridge.sh
```

等价的显式写法是 `./scripts/start_mobile_bridge.sh restart --robot x2 --host 0.0.0.0 --port 8765
--source mobile_app`。脚本默认用 `nohup` 脱离 SSH/终端运行；检查状态执行
`./scripts/start_mobile_bridge.sh status`，停止执行 `./scripts/start_mobile_bridge.sh stop`。
日志默认写入 `/tmp/x2-ps5-teleop-$UID.log`。

脚本会自动设置 `PYTHONPATH`，并检查当前用户是否为 `run`、AimDK 环境以及
`websockets`、`numpy`、`rclpy` 和 `aimdk_msgs` 是否可导入。PC2 使用手机热点时 IP
通常是动态地址，脚本不依赖固定的 `10.0.1.41`；`aimdk_msgs` 的 Python 文件依赖 `numpy`，所以真机模式必须先
在项目目录执行 `uv sync`。加载官方 `cobridge/setup.bash` 时，脚本会兼容其中读取未定义
环境变量的写法，不会修改官方环境文件。若 `cobridge/setup.bash` 不在默认路径，可使用：

```bash
./scripts/start_mobile_bridge.sh --robot x2 \
  --cobridge /实际路径/cobridge/setup.bash
```

从 `agi` 会话启动时使用 `sudo -iu run` 包装上述命令。桥接服务启动后为 `IDLE`，不会
自动解锁。首次真机测试顺序为：确认 ROS 服务可用 → 手机连接并保持 IDLE → 释放物理急停
并确认机器人稳定 → 进入 TELEOP → 极低速短时移动 → 退出 TELEOP 并确认零速度。

### App 生命周期与并发安全

App 以 20 Hz 发送速度帧。服务端使用单控制租约、递增序列号和 0.4 秒看门狗：不同手机
不能同时控制；同一手机的新连接会使旧连接失效；重复/乱序帧会拒绝；超时、断连、切后台
和连接替换都会停车。急停必须明确清除后重新解锁。

### 移动端构建

`mobile/app.json` 已配置横屏、本地网络权限和 Android 明文局域网 WebSocket。开发期可用
Expo Go；需要稳定部署时使用 Development Build 或 EAS Build。构建后仍需在现场网络中
确认手机可以访问 PC2 的 TCP `8765` 端口。

生成 Android APK：

```powershell
cd mobile
npx eas-cli login
npx eas-cli build --platform android --profile preview
```

项目中的 `mobile/eas.json` 已将 `preview` 配置为 APK。安装打包后的 App 后，不再需要
Expo Go 或 Metro 的 `8081` 端口；App 可自动发现 PC2，也可以在界面中填写 PC2 的 WebSocket 地址，例如
`ws://10.0.1.41:8765`。EAS 构建本身需要访问 Expo 云服务，但打包后的 App 控制连接
只需要手机能够访问 PC2 的 `8765` 端口。

若 EAS APK 安装后无法连接，确认 PC2 桥接监听 `0.0.0.0:8765`，手机与 PC2 处于同一个非隔离 Wi-Fi，并在防火墙放行 TCP 8765；不要填写 `localhost` 或 `127.0.0.1`。iOS 首次自动发现必须允许“本地网络”。

## 机器人侧

注意：官方蓝牙配对会把 DualSense HID 设备绑定到 PC1（`10.0.1.40`），由
`soc0_rc` 在 PC1 内部消费；现场没有 `/joy` 或公开的 PS5 输入 Topic。PC2 上运行的
`pygame` 读取器只能看到配对到 PC2 的手柄。不要在 PC1 启动本项目来绕过这一限制；如需
保留官方配对方式，应先向现场确认可用的输入转发接口。

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
