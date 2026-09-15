# Expo React Native 手机遥操作方案

本文档记录将本项目从 DualSense 输入改为手机 App 输入的方案，供后续对话和现场部署继续使用。

## 结论

手机 App 可行，但手机不应直接连接 ROS 2，也不应直接调用 PC1 的 MC/EtherCAT 接口。推荐在
PC2（`10.0.1.41`）增加 WebSocket 桥接服务：手机只连接 PC2，PC2 复用本仓库的 AimDK ROS
适配层向机器人发送命令。

```text
Expo React Native App
        │ WebSocket（同一局域网）
        ▼
PC2 10.0.1.41：teleop bridge（run 用户）
        │ ROS 2 / AimDK
        ▼
PC1 MC / EtherCAT ── X2
```

官方 DualSense 可以继续连接机器人原生蓝牙，不需要让手机接管手柄，也不需要在 PC1 运行二开程序。

## 网络拓扑

### 同一无线 Wi-Fi（推荐）

手机和 PC2 连接同一个无线 AP，手机访问 PC2 的 WebSocket，例如：

```text
ws://10.0.1.41:8765
```

AP 必须允许无线客户端互访，不能启用 client/AP isolation。PC2 到机器人 ROS 网络的既有路由必须
保持不变。手机不需要直接访问 PC1。

### 手机热点

也支持手机开启热点、让 PC2 加入热点。但此时 PC2 会获得新的热点地址，且可能出现多网卡和路由
优先级问题。需要确认：

- 手机能够访问 PC2 在热点上的地址；
- 热点允许设备之间通信；
- PC2 仍能通过原有接口发现和访问机器人 ROS 2 图；
- WebSocket 服务监听 `0.0.0.0`，而不是只监听 `127.0.0.1`。

手机热点适合作为无现场 Wi-Fi 时的备用方案，不建议作为第一次真机测试的唯一网络。

### 不建议的方式

不要把控制端口暴露到公网，也不要假设连接 X2 自带 Wi-Fi 就一定能访问 PC2。必须实测手机到
PC2 的 TCP 连接和 PC2 到 ROS 图的连通性。

## Expo App 技术选型

- 框架：Expo + React Native（TypeScript）
- 通信：WebSocket，建议 20 Hz 发送控制帧；状态和告警由服务端推送
- 手势：React Native Gesture Handler 或 PanResponder 实现虚拟摇杆
- 反馈：`expo-haptics` 提供急停、断连和未解锁提示
- 开发期：Expo Go 可用于 UI 和网络联调；正式 APK/ipa 应使用 Development Build 或 EAS Build
- ROS 2 不放在手机端，避免在 Expo 中引入 DDS、`rclpy` 或原生 AimDK 依赖

建议目录：

```text
mobile/                 Expo React Native App
src/x2_ps5_teleop/bridge/  PC2 WebSocket 服务
```

## App 功能

### 移动控制

- 左侧虚拟摇杆：前后（`forward_velocity`）、横移（`lateral_velocity`）
- 右侧虚拟摇杆：旋转（`angular_velocity`）
- 速度上限沿用当前配置，首轮仍为 `0.12 m/s`、`0.08 m/s`、`0.15 rad/s`
- 摇杆松手立即发送零速度
- 需要点击“进入 TELEOP”后才接受运动输入
- App 失焦、锁屏、切后台或 WebSocket 断开时自动停车

### 运动模式按钮

按钮映射使用已确认的 `SetMcAction` 模式字符串：

| App 按钮 | AimDK 模式 |
|---|---|
| 被动 | `PASSIVE_DEFAULT` |
| 阻尼 | `DAMPING_DEFAULT` |
| 关节 | `JOINT_DEFAULT` |
| 站立 | `STAND_DEFAULT` |
| 行走 | `LOCOMOTION_DEFAULT` |

实际按钮名称和可用模式必须以现场固件为准。

### 灵巧手预设动作

预设动作按钮通过 PC2 发布 `HandCommandArray`。当前已确认：

- Topic：`/aima/hal/joint/hand/command`
- 类型：`aimdk_msgs/msg/HandCommandArray`
- 左右手类型：`NIMBLE_HANDS = 1`
- 每只手 10 个命令槽
- 左手拇指前三个槽的位置符号需要镜像

预设动作必须一次只执行一个。发送前先停车，动作后回到 `IDLE`，需要再次解锁才能移动。
位置值（例如官方示例中的 `0.8`）不是现场安全极限，必须根据状态反馈、单位和关节限位标定。

### 灵巧手直接调整

App 可以提供左右手各 10 个槽位的逐项滑块或数值输入，并发送：

```text
name
position
velocity
acceleration
deceleration
effort
```

但在实现“直接调整”前必须现场确认每个槽位的官方名称、关节顺序、单位、方向、位置和速度
限位。未确认前只能提供预设按钮，不能把滑块直接映射到真实手指。

直接调整界面应具备：

- 单手/双手选择；
- 每个关节独立限幅；
- “发送单帧”与“连续发送”两个明确模式；
- 松手或断连自动停止连续发送；
- 现场反馈值与目标值同时显示；
- 软限位、速度限幅和物理急停提示。

## 建议 WebSocket 协议

客户端发送速度帧：

```json
{
  "type": "velocity",
  "forward": 0.0,
  "lateral": 0.0,
  "angular": 0.0,
  "sequence": 123,
  "timestamp_ms": 1710000000000
}
```

客户端发送按钮或模式事件：

```json
{"type": "mode", "mode": "LOCOMOTION_DEFAULT"}
{"type": "preset", "action": "right_wave"}
{"type": "estop"}
{"type": "arm", "enabled": true}
```

服务端应推送：

```json
{
  "type": "state",
  "state": "TELEOP",
  "armed": true,
  "source": "mobile_app",
  "last_command_age_ms": 42,
  "robot_connected": true
}
```

直接手部调整可使用显式的 `hand_target` 消息；服务端不得接受未通过限位校验的值。协议需要
版本号和序列号，服务端拒绝乱序或过期帧。

## PC2 桥接服务安全要求

- 以官方 `run` 用户运行；不在 PC1 运行二开程序
- 启动时保持 `IDLE`，不能自动进入 `TELEOP`
- 每个速度帧都进行死区、限幅和数值合法性校验
- 心跳和速度帧超过 `0.3–0.5 s` 未更新时发布零速度
- WebSocket 断开、App 切后台、服务异常时发布零速度
- 急停状态锁存，必须明确清除并重新解锁
- 输入源使用独立名称（例如 `mobile_app`），优先级和超时以现场确认值为准
- 记录连接、解锁、急停、模式切换和手部命令日志，但不得记录密码
- 首轮只做低速、短时、空旷环境测试，物理急停必须有人值守

## 已确认的现场事实与踩坑

- PC1 `10.0.1.40` 运行 `mc_app_main`、EtherCAT、`soc0_rc` 等原生运控程序，禁止部署二开程序。
- PC2 `10.0.1.41` 是二开部署位置，官方环境需切换到 `run` 用户并加载
  `/agibot/software/cobridge/setup.bash`。
- `agi` 用户无法读取 `/agibot/software/cobridge`，直接执行 `ros2` 会显示找不到命令；必须使用
  `sudo -iu run`。
- 官方蓝牙配对的 DualSense 出现在 PC1 的 `js0`，PC2 的 `pygame joysticks=0` 是正常现象。
- ROS 图没有 `/joy`，`soc0_rc` 直接发布 `/aima/mc/locomotion/velocity`；
  `/robot/ps5_controller/capacity` 只是容量状态，不是按键输入。
- 不能凭经验猜测 PS5 输入 Topic，也不能通过在 PC1 启动二开程序来绕过原生链路。
- PC2 目录历史上存在根目录旧脚本和 `src/` 新源码不一致的问题；部署后必须以 `PYTHONPATH=$PWD/src`
  运行为准，并同步完整源码。
- `GetHandType` 现场字段为 `left_hand` / `right_hand`；错误使用 `left_hands_type` /
  `right_hands_type` 会在启动时失败。
- 手部命令监听状态时需要 BEST_EFFORT QoS；位置示例值不能直接当作安全极限。
- 远端检查应针对已确认 IP、低频、只读执行；不要并发扫描、无限重试、保存 SSH 密码或使用
  `ssh -tt`。

## 实施顺序

1. 在 PC2 实现 WebSocket 桥接服务，只接收速度、解锁、急停和模式事件。
2. 用 MockRobot 和协议测试验证断连、超时、乱序、急停和限幅。
3. 创建 Expo App，实现连接页、虚拟摇杆、解锁/急停和模式按钮。
4. 手机与 PC2 同一 Wi-Fi 联调，只验证服务状态和零速度，不连接真机运动。
5. 在现场确认输入源仲裁和机器人安全状态后，进行极低速移动测试。
6. 增加预设手部动作，逐个测试并确认动作完成后回到 `IDLE`。
7. 读取 `/aima/hal/joint/hand/state`，确认关节顺序、单位和限位后，再开发直接调整滑块。

## 当前实现

仓库现在包含：

- `src/x2_ps5_teleop/bridge/`：PC2 WebSocket 桥接服务，入口为 `x2-teleop-bridge`；
- `mobile/`：Expo React Native TypeScript App；
- `tests/test_bridge.py`：桥接并发、序列号、看门狗、急停和预设动作测试。

桥接服务采用单控制租约。同一时刻只允许一个 `client_id`，同一设备建立新连接时旧
WebSocket 会被关闭；服务端用连接 session id 再检查一次控制权，因此旧连接的延迟帧
不会覆盖新连接。速度帧由服务端按 0.12 / 0.08 / 0.15 的上限再次限幅，连续控制
超过 0.4 秒未收到帧会发布零速度并进入 `TIMEOUT`。

本地联调：

```powershell
uv sync
uv run pytest
uv run x2-teleop-bridge --robot mock
cd mobile
npm install
npm start
```

手机与 PC2 同网后，将 App 中地址改为 `ws://PC2地址:8765`。首轮只使用 `--robot mock`
或只观察桥接状态，不要直接进行真机移动测试。

## 手机 App 使用流程

### 本地 Mock 联调

在 PC2 或开发电脑上启动桥接服务：

```powershell
uv sync
uv run pytest
uv run x2-teleop-bridge --robot mock --host 0.0.0.0 --port 8765
```

另开终端启动 Expo：

```powershell
cd mobile
npm install
npm run typecheck
npm start
```

使用 Expo Go 或 Development Build 打开 App。手机与桥接服务电脑连接同一个允许客户端
互访的 Wi-Fi，在 App 地址栏输入 `ws://<电脑局域网IP>:8765`，点击“连接”。Mock 模式
不会连接机器人，只会在桥接服务终端打印 `MOVE`、模式和手部动作事件。

### 真机部署

真机桥接服务只能部署到 PC2（`10.0.1.41`）或已确认能访问 ROS 图的外部上位机，不能
部署到 PC1（`10.0.1.40`）。使用官方 `run` 用户和 AimDK 环境：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
x2-teleop-bridge --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app
```

手机填写 `ws://10.0.1.41:8765` 或现场实际的 PC2 局域网地址。第一次真机测试前应先
完成 Mock 联调，并确认：手机能访问 PC2 的 TCP 8765、PC2 能发现 AimDK ROS 图、物理
急停有人值守、机器人周围无人且状态稳定。

### App 操作

1. 查看顶部状态为“已连接”，并确认机器人状态为 `IDLE · 未解锁`。
2. 点击“进入 TELEOP”，状态变为 `TELEOP · 已解锁` 后才会接受摇杆。
3. 左摇杆控制前后和横移，右摇杆控制旋转；松开摇杆会持续发送零速度帧。
4. 运动模式按钮发送 `PASSIVE_DEFAULT`、`DAMPING_DEFAULT`、`JOINT_DEFAULT`、
   `STAND_DEFAULT` 或 `LOCOMOTION_DEFAULT`。
5. 手部预设动作会先停止底盘，动作完成后回到 `IDLE`，必须重新进入 TELEOP。
6. 发现异常时点击“急停”；急停锁存，确认安全后才允许清除并重新解锁。

### 连接和并发规则

- 桥接服务只授予一个控制租约。另一台手机连接时会收到 `busy`。
- 同一手机重新建立连接时，新连接会替换旧连接；旧连接收到的延迟帧不会再进入机器人。
- 每个控制消息带递增 `sequence`，重复或乱序消息会被拒绝。
- App 每 50 ms 发送速度帧；服务端超过 0.4 秒未收到速度/心跳会发零速度并进入 `TIMEOUT`。
- App 切后台、锁屏、关闭连接或网络断开都会停止控制；服务端断开处理也会停车。

### 常见问题

| 现象 | 检查项 |
|---|---|
| App 一直连接失败 | 手机与 PC2 是否同一 Wi-Fi；AP 是否启用了 client isolation；服务是否监听 `0.0.0.0:8765`；防火墙是否允许 8765 |
| 收到 `busy` | 另一台手机仍占用控制，或旧连接尚未释放；关闭旧 App 后重新连接 |
| 已连接但不能移动 | 必须先点击“进入 TELEOP”；确认服务状态不是 `ESTOP`、`TIMEOUT`，且物理急停已释放 |
| 一拖摇杆就回到 TIMEOUT | 检查手机是否切后台、网络是否抖动，PC2 时间/负载是否异常；速度帧必须持续到达 |
| 真机服务启动失败 | 确认使用 `run` 用户、已 source `cobridge/setup.bash`，并检查 AimDK 服务是否可用 |
| iPhone 无法访问 | 使用 Development Build/EAS Build，确认本地网络权限；在同一 Wi-Fi 下重试，不要使用公网地址 |

