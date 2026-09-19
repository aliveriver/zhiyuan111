# X2 上肢轨迹与手机遥操作对接文档

本文面向需要开发、部署、联调或维护本项目的工程人员。目标是说明手机 App、PC2 桥接、AimDK ROS 2、MC、HAL 和轨迹文件之间的接口契约。文档中的“已实现”表示代码和离线测试已经覆盖；“已确认”表示在当前 v0.9.7 环境中有接口或只读证据；“待现场确认”不能当作可用能力。

## 1. 系统边界

```text
DualSense（可选）或手机 App
        |
        | WebSocket protocol_version=1
        v
PC2 10.0.1.41, run 用户
  x2-ps5-teleop.bridge.server
        |
        | ROS 2 / AimDK
        v
PC1/soc0 10.0.1.40
  MC -> HAL/EtherCAT -> X2
```

手机不连接 ROS 2，不连接 PC1，不直接调用 EtherCAT。PC1 保留官方 MC、HAL 和系统状态机；本项目的真机桥接只部署在 PC2。PC2 崩溃或 PC2 与 MC 失联时，MC 可能继续执行已经加载的动画，物理急停仍是最终保护。

当前仓库的默认能力如下：

| 能力 | Mock | X2 v0.9.7 默认 |
| --- | --- | --- |
| 底盘速度和模式 | 可用 | 已恢复，需现场状态正常 |
| 灵巧手五参数/位置目标 | 模拟 | 已恢复，仍需观察 MC 仲裁 |
| 双臂 14 + 双手 20 状态录制 | 模拟反馈 | 只读采集，不卸力 |
| MC 上肢 CSV 回放 | 模拟 | 开放；无报告时为有人值守测试模式 |
| 卸力拖动示教 | 不提供 | 未确认，不提供 |

启动 X2 时可以省略 `--mc-commissioning-profile`，此时上肢回放采用保守限制并在 App 显示未 commissioning 警告。运行时站立、MC 空闲、起点、速度和停止反馈保护不因省略报告而关闭。

## 2. 代码结构

| 路径 | 责任 |
| --- | --- |
| `src/x2_ps5_teleop/core.py` | DualSense 轴、按钮、速度和模式数据结构 |
| `src/x2_ps5_teleop/teleop/state_machine.py` | IDLE、TELEOP、ESTOP、断连、超时状态机 |
| `src/x2_ps5_teleop/robot/motion.py` | MockRobot 和 X2RosRobot；ROS topic/service 适配 |
| `src/x2_ps5_teleop/bridge/server.py` | WebSocket 服务、连接租约、参数和数据目录 |
| `src/x2_ps5_teleop/bridge/controller.py` | 消息校验、互锁、手部预设、录制、播放控制 |
| `src/x2_ps5_teleop/bridge/hand_poses.py` | 手部位置预设持久化和校验 |
| `src/x2_ps5_teleop/robot/mc_animation.py` | 上肢 CSV 白名单、插值和 500 Hz 重采样 |
| `src/x2_ps5_teleop/robot/mc_playback.py` | 现场验收门控、上传、MC 状态、暂停/停止和互锁 |
| `src/x2_ps5_teleop/robot/mc_animation_probe.py` | 只读状态探针、诊断资源和现场单关节候选测试 |
| `src/x2_ps5_teleop/robot/mc_playback_trial.py` | 现场验收后的单次 trial；默认只生成预览 |
| `mobile/App.tsx` | Expo React Native App；只连接 WebSocket |
| `tests/` | Python 单元、协议、动画和播放竞态测试 |

## 3. WebSocket 协议

### 3.1 连接和通用规则

客户端首帧必须是：

```json
{"type":"hello","protocol_version":1,"client_id":"mobile-unique-id"}
```

服务端返回 `hello_ack`，随后推送 `state`。除 `hello` 外的客户端消息都带非负、严格递增的 `sequence`。重复、乱序或非整数序列号被拒绝。每条连接有随机 `session_id`；同一 `client_id` 重连时新会话替换旧会话，其他客户端收到 `busy`。

服务端返回的控制状态包含：

```json
{
  "type":"state",
  "state":"TELEOP",
  "armed":true,
  "source":"mobile_app",
  "last_command_age_ms":42,
  "robot_connected":true,
  "playback_state":"idle",
  "playback_name":null,
  "playback_progress_ms":0,
  "playback_duration_ms":0,
  "playback_error":null,
  "control_capabilities":{}
}
```

状态里的 `playback_progress_ms` 在 MC 后端是估算值，因为 MC 有启动和中断过渡，不是精确的机器人帧号。

### 3.2 底盘、模式和急停

```json
{"type":"arm","enabled":true,"sequence":1}
{"type":"velocity","forward":0.0,"lateral":0.0,"angular":0.0,"sequence":2}
{"type":"mode","mode":"LOCOMOTION_DEFAULT","sequence":3}
{"type":"heartbeat","sequence":4}
{"type":"estop","sequence":5}
{"type":"clear_estop","sequence":6}
```

速度服务端再次限幅，默认上限为 `forward=0.12 m/s`、`lateral=0.08 m/s`、`angular=0.15 rad/s`。App 每 50 ms 发送速度帧；约 0.4 s 没有有效速度/心跳时进入 `TIMEOUT` 并请求零速度。`estop` 是软件锁存，不切断电机电源；清除后仍需重新 `arm`。

可用模式字符串为 `PASSIVE_DEFAULT`、`DAMPING_DEFAULT`、`JOINT_DEFAULT`、`STAND_DEFAULT` 和 `LOCOMOTION_DEFAULT`。模式由官方 `SetMcAction` 服务执行；模式是否适合当前动作以现场固件状态为准。

### 3.3 灵巧手

位置目标：

```json
{"type":"hand_positions","side":"right","positions":[0,0,0,0,0,0,0,0,0,0],"sequence":10}
```

旧版五参数目标：

```json
{"type":"hand_target","side":"left","joints":[
  {"index":0,"position":0,"velocity":0.1,"acceleration":0,"deceleration":0,"effort":0}
],"sequence":11}
```

实际数组必须有 10 项。位置编辑入口接受 `-pi..pi` 的有限 rad 值；五参数入口接受代码中的应用范围。范围是输入校验，不是已标定硬件限位。旧版入口保留左手前三槽的历史符号变换；反馈位置预设入口不再二次镜像。

手部预设消息包括 `hand_pose_list`、`hand_pose_save`、`hand_pose_delete`、`hand_pose_rename`、`hand_pose_apply` 和 `hand_state`。带 `requires_confirmation=true` 的松手预设必须发送 `confirmed=true`。目标发送成功只表示消息已发出，不表示抓稳或接稳。

播放或暂停活动处于 `preparing`、`playing`、`pausing`、`paused`、`stopping`、`stop_failed` 时，手部目标、手部预设、模式切换和新轨迹都被互锁。递出和松手应拆成两个步骤：递出轨迹完成后人工确认接稳，再执行松手预设。

### 3.4 录制和轨迹管理

开始录制：

```json
{"type":"trajectory_record_start","name":"双手递出","sample_rate_hz":20,"teach_mode":false,"sequence":20}
```

`sample_rate_hz` 为 1--100 Hz。录制采集真实反馈中的 14 个臂关节、左手 10 个主动轴和右手 10 个主动轴，不采集腿、腰、头或底盘速度。当前 App 固定使用 `teach_mode=false`；该选项不应被理解为可用的卸力示教。

停止保存：

```json
{"type":"trajectory_record_stop","sequence":21}
```

列表、保存、删除和改名消息分别为 `trajectory_list`、`trajectory_save`、`trajectory_delete`、`trajectory_rename`。同名不会覆盖。数据默认位于 `~/.x2_ps5_teleop/trajectories.json`，手部预设位于 `~/.x2_ps5_teleop/hand_poses.json`。写入使用临时文件替换；损坏文件不会被静默覆盖。

### 3.5 播放状态

```json
{"type":"trajectory_play","name":"双手递出","speed":0.25,"sequence":30}
{"type":"trajectory_play","command":"pause","sequence":31}
{"type":"trajectory_play","command":"resume","sequence":32}
{"type":"trajectory_play","command":"stop","sequence":33}
```

Mock 保留逐帧播放。真机 MC 后端执行以下步骤：

1. 校验固件、验收报告、远端库和配置哈希。
2. 验证 `Business`、`STAND_DEFAULT`、`RUNNING`、MC player 状态和新鲜上肢反馈。
3. 将录制帧按速度插值为每 2 ms 一行的 CSV，只导出 14 臂＋20 手的 `command_pos::` 列。
4. 通过 SSH 上传 soc0 临时文件，再调用 `SetMcPresetMotion(ani_path=...)`。
5. 观察已验收的 MC 状态序列和末端反馈，才能标记 completed。

暂停/停止使用当前位置生成恒定姿态动画，以 `interrupt=true` 替换；RPC 返回成功不足以证明停止。RPC 超时也可能已经开始运动，因此仍会进入停止处理。保持上传期间姿态变化、反馈过期、状态序列不匹配或稳定位置不满足要求时进入 `stop_failed`，留下 `mc-motion-unconfirmed.lock`，锁住后续动作。不能手工删除该文件绕过互锁。

## 4. ROS 2 对接表

| 方向 | 名称 | 类型/服务 | 当前用途 |
| --- | --- | --- | --- |
| 发布 | `/aima/mc/locomotion/velocity` | `aimdk_msgs/msg/McLocomotionVelocity` | 官方底盘速度 |
| 服务 | `/aimdk_5Fmsgs/srv/SetMcAction` | `SetMcAction` | 官方模式/动作 |
| 服务 | `/aimdk_5Fmsgs/srv/SetMcInputSource` | `SetMcInputSource` | 注册 `mobile_app` 输入源 |
| 服务 | `/aimdk_5Fmsgs/srv/GetHandType` | `GetHandType` | 启动时确认左右 NIMBLE_HANDS |
| 服务 | `/aimdk_5Fmsgs/srv/GetSystemState` | `GetSystemState` | 系统状态检查 |
| 发布 | `/aima/hal/joint/hand/command` | `aimdk_msgs/msg/HandCommandArray` | 已恢复的手部目标路径 |
| 订阅 | `/aima/hal/joint/arm/state` | `JointStateArray` | 录制和起点检查 |
| 订阅 | `/aima/hal/joint/hand/state` | `HandStateArray` | 录制和手部反馈 |
| 订阅 | `/aima/mc/common/state` | `McCommonState` | MC action/player 状态 |
| 发布/订阅 | `/aima/hal/joint/arm/command` | `JointCommandArray` | 存在但当前真机不用于 MC 回放 |
| 服务 | `/aimdk_5Fmsgs/srv/PlayTts` | `PlayTts` | 固定文字语音（可选，需固件提供） |
| 服务 | `/aimdk_5Fmsgs/srv/PlayAudioFile` | `PlayAudioFile` | 固定 WAV/PCM 音频（可选，文件在 PC3） |

MC 当前也发布手臂和手部 HAL 命令。向这些命令 topic 叠加发布会产生控制竞争；不能用它绕过 MC 控制权。

### 4.1 固定语音

APP 发送 `voice_play`，Bridge 根据 `config/voice_presets.json` 选择官方 `PlayTts` 或 `PlayAudioFile`。该请求不调用底盘停止接口，和移动速度控制并行；语音播放期间仍由现有速度帧和看门狗负责移动安全。`hello_ack` 返回 `voice_presets`，因此 APP 按配置条目动态生成按钮。

```json
{"type":"voice_play","preset":"ten_years_review","sequence":42}
```

`PlayAudioFile` 的 WAV/PCM 文件必须在 PC3 可读目录，当前配置对应 `十年赛事回顾.wav` 和 `总结十年赛事.wav`。当前硬件 `release-lx2501_3_t2d5-soc0-v0.9.7` 未连接验证，若 AimDK 未提供可选语音服务，Bridge 只拒绝语音请求，不影响既有移动接口。

## 5. 配置和部署

### 5.1 Mock

```bash
cd /home/run/zhiyuan111
PYTHONPATH="$PWD/src" .venv/bin/python -m x2_ps5_teleop.bridge.server \
  --robot mock --host 0.0.0.0 --port 18765 \
  --data-dir /tmp/x2-award-mock-data \
  --config /home/run/zhiyuan111/config/controller.json
```

手机连接 `ws://PC2:18765`。Mock 不初始化 ROS，不连接机器人；不要把模拟轨迹写入生产数据目录。

### 5.2 X2

现场准备好后，使用 PC2 的 `run` 用户：

```bash
cd /home/run/zhiyuan111
source /agibot/software/cobridge/setup.bash
export PYTHONPATH="$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages"
export AMENT_PREFIX_PATH="/agibot/software/common:/agibot/software/ec:${AMENT_PREFIX_PATH:-}"
export LD_LIBRARY_PATH="/agibot/software/common/lib:/agibot/software/ec/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="/agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so${LD_PRELOAD:+:$LD_PRELOAD}"
.venv/bin/python -m x2_ps5_teleop.bridge.server --robot x2 --host 0.0.0.0 --port 8765 \
  --source mobile_app --config /home/run/zhiyuan111/config/controller.json
```

现场验收完整且配置放在 `/home/run/.x2_ps5_teleop/mc-commissioning.json` 后，可在命令末尾增加以下可选参数，启用报告和远端哈希核对：

```text
--mc-commissioning-profile /home/run/.x2_ps5_teleop/mc-commissioning.json
```

当前现场未就绪时不要执行 X2 命令，不要自动重启正在运行的桥接。部署、备份、同步和验收顺序见 [PC2_DEPLOYMENT.md](PC2_DEPLOYMENT.md) 与 [MC_ANIMATION_COMMISSIONING.md](MC_ANIMATION_COMMISSIONING.md)。

### 5.3 App

```bash
cd mobile
npm ci
npm run typecheck
npm start
```

App 地址填写 `ws://10.0.1.41:8765` 或现场 PC2 地址。手机只需要访问 PC2；不需要访问 PC1。手机切后台、锁屏或 WebSocket 断开时会关闭连接，服务端随后执行断连处理。

## 6. 联调顺序

1. 运行 `.venv/bin/python -m pytest -q`。
2. 运行 `npm run typecheck`，必要时运行 `npx expo export --platform android`。
3. Mock 桥接使用独立数据目录，验证连接、解锁、速度限幅、模式、手部预设、录制、改名、删除、重复播放、暂停、继续、停止和急停。
4. 只读检查 X2 状态和反馈，不发布运动命令；确认 `Business / STAND_DEFAULT / RUNNING` 前提时必须由现场人员准备。
5. 使用 `mc_animation_probe` 默认模式生成本地 CSV 和报告；默认不上传、不调用 MC 控制服务。
6. 现场首轮只做空载单关节小幅测试和停止验证，再用 `mc_playback_trial` 验证暂停、继续、断连、超时和完整 0.25x 轨迹。
7. 验收人审阅报告、哈希、状态序列和腿腰头隔离后，才加载正常桥接配置开放回放。

## 7. 故障处理

| 现象 | 处理 |
| --- | --- |
| `busy` | 关闭旧 App 或等待旧会话释放；不要同时启动两个桥接 |
| `not_armed` | 先进入 TELEOP；急停需先清除再重新解锁 |
| 手部按钮灰色 | 检查后端能力、TELEOP 和是否有播放/停止互锁 |
| 缺少录制帧 | 只读检查 arm/state、hand/state、QoS 和状态新鲜度 |
| MC 配置拒绝 | 检查验收人、报告哈希、远端库/配置哈希、枚举和状态序列 |
| `stop_failed` | 保持物理急停值守，查看反馈和 trace，明确点击重试停止；不能删互锁文件 |
| 真机播放按钮灰色 | 默认门控行为；离线测试通过不代表现场验收完成 |
| App 无法连接 | 检查手机与 PC2 路由、防火墙、监听地址和 AP client isolation |

## 8. 参考文件

- [APP_USER_GUIDE.md](APP_USER_GUIDE.md)：操作员流程。
- [PC2_DEPLOYMENT.md](PC2_DEPLOYMENT.md)：备份、同步和部署。
- [UPPER_BODY_TRAJECTORY.md](UPPER_BODY_TRAJECTORY.md)：轨迹格式和录制边界。
- [MC_ANIMATION_COMMISSIONING.md](MC_ANIMATION_COMMISSIONING.md)：现场验收和配置字段。
- [X2_V0_9_7_CONTROL_INVESTIGATION.md](X2_V0_9_7_CONTROL_INVESTIGATION.md)：接口调查和证据。
- [mc_commissioning.example.json](mc_commissioning.example.json)：默认关闭的配置模板。
