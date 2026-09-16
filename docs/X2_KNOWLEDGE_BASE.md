# AgiBot X2 v0.9.7 知识库

本文是当前项目维护范围内的 X2 知识汇总。它记录当前设备、软件、接口、控制权、安全边界和已知未知项，供开发、部署、验收和故障排查使用。它不是厂商完整维修手册，也不能替代现场人员、物理急停或厂商确认。

## 1. 设备身份和网络

| 项目 | 当前事实 |
| --- | --- |
| 机器人 | AgiBot X2 Ultra，产品配置 `lx2501_3_t2d5` |
| 固件 | Agi v0.9.7，接口版本 `v0.9.0.7` |
| PC1 / soc0 | `run@10.0.1.40`，运行系统状态机、MC、EtherCAT 和官方遥控链路 |
| PC2 | `run@10.0.1.41`，运行本项目桥接和手机 App 的网络入口 |
| 轨迹数据 | `/home/run/.x2_ps5_teleop/trajectories.json` |
| 手部预设 | `/home/run/.x2_ps5_teleop/hand_poses.json` |
| ROS | Ubuntu 22.04 / ROS 2 Humble / AimDK 运行环境 |
| 当前只读状态记录 | `Business / PASSIVE_DEFAULT`；这不等于稳定站立 |

PC1 是官方运控计算机，不部署二开桥接。PC2 通过 ROS 2 图访问官方接口。PC2 的开发 Python 必须优先加载 `/agibot/software/common` 的完整 `aimdk_msgs`，不能让精简的 `ec` 同名包遮蔽它。

## 2. 系统状态和动作

系统状态机已读到 `Startup`、`Ready`、`Business`、`EStop`、`OTA`、`ProductionTest`、`Calibration`、`AgeingTest` 和 `Poweroff`。当前配置没有 `Develop_MC`。官方更新资料将开发者模式列为更高版本能力，因此不能把它当成 v0.9.7 的解决方案。

系统状态迁移服务存在，但调查阶段没有试切。`Business` 或 `Ready` 会激活整个 Motion 功能组，不提供“只释放双臂、保留腿部 MC”的已确认公开模式。生产、标定、OTA 和 Poweroff 不能替代该模式。

常见官方动作名包括：

| 动作 | 项目中的理解 |
| --- | --- |
| `PASSIVE_DEFAULT` | 被动状态；实际状态需要反馈确认 |
| `DAMPING_DEFAULT` | 阻尼动作；不是本项目的灵巧手卸力证明 |
| `JOINT_DEFAULT` | 官方位控动作；不等于自由拖动 |
| `STAND_DEFAULT` | 稳定站立动作，配置中含 MC planner/animation player |
| `LOCOMOTION_DEFAULT` | 官方行走动作 |

动作名称和数值枚举必须使用安装消息和反馈确认，不能只根据文档或字符串猜测。

## 3. 运控架构

```text
官方输入 / mobile_app
        v
SetMcInputSource + MC 输入仲裁
        v
MC（站立、行走、手臂、灵巧手、动画 runner）
        v
HAL joint command / EtherCAT
        v
腿、腰、臂、手执行器
```

已确认的主要路径：

- `/aima/mc/locomotion/velocity` 输入底盘速度。
- MC 在当前配置中发布手臂命令和灵巧手命令；手臂约 500 Hz、灵巧手约 50 Hz 的配置证据不能替代现场频率测量。
- `/aima/hal/joint/arm/command` 和 `/aima/hal/joint/hand/command` 实际存在，但向其叠加发布会与 MC 竞争。
- 当前 v0.9.7 没有发现 `/mc/upper_body_command` 或 `UpperBodyCommandArray`。
- `SetMcPresetMotion` 有 `area`、`motion`、`interrupt`、`ani_path`；`area` 是动作请求参数，不是 HAL 控制权开关。
- `GetMcPresetMotionState` 不能独立证明暂停、停止、保持或失败已经完成。

### 控制权原则

MC 负责站立和机械臂控制；本项目没有确认到安全的手臂 HAL 控制权释放接口。即使能够构造 HAL 消息，删除保护或同时发布也会形成竞争。当前采用的真机候选路径是“CSV 资源 → MC 官方动画执行”，保留 `_require_develop_mc()` 和现场配置门槛。

## 4. 机器人控制输入

### DualSense

本地状态机默认映射：

| 输入 | 功能 |
| --- | --- |
| 左摇杆 Y | 前后，默认上限 0.12 m/s |
| 左摇杆 X | 横移，默认上限 0.08 m/s |
| 右摇杆 X | 偏航，默认上限 0.15 rad/s |
| `OPTIONS` | IDLE/TELEOP 切换 |
| `PS` | 锁存软件急停 |
| `Cross` / `Circle` / `Triangle` / `Square` | 被动/阻尼/关节/站立 |
| `L1` / `R1` / `LT` / `RT` | 配置化手部/动作事件 |

SDL 轴和按钮编号可能随设备或驱动变化，先运行输入诊断，再改 `config/controller.json`。官方 DualSense 如果直接配对到 X2，设备出现在 PC1；PC2 的 `pygame joysticks=0` 是预期现象。本项目手机 App 不依赖这条原生蓝牙输入链路。

### 手机 App

手机只连接 PC2 WebSocket。服务端提供控制租约、递增序列号校验、速度限幅、心跳超时、断连处理和软件急停。App 切后台、锁屏或 WebSocket 断开时停止发送；服务端收到断连后请求零速度和活动停止。

## 5. 灵巧手

当前 `GetHandType` 只读确认左右手类型值为 `1`，代码将其解释为 `NIMBLE_HANDS`。每只手有 10 个主动自由度；实机反馈名称为空时，项目按数组顺序保留槽位和符号。

当前已恢复的手部路径是：

```text
App -> BridgeController -> X2RosRobot
     -> /aima/hal/joint/hand/command
     -> 现有 HAL/MC
```

两种编辑入口：

1. 五参数目标：position、velocity、acceleration、deceleration、effort。它兼容原有消息路径，不能据 `effort` 字段推断力控或卸力。
2. 反馈位置预设：读取实际位置，逐槽编辑并保存；发送时不做左手二次镜像，输入边界 `+-pi` 只是应用校验。

手部目标成功发送不表示抓稳。官方 MC 可能覆盖或争抢手部目标；出现回弹、抖动或目标不一致时，应停止操作并记录模式，不提高频率抢占。

### O10 文档能确认什么

官方 OmniHand O10 手册给出独立设备协议：CAN-FD/串口命令 `0x01` 的数据 `0x00` 为失能、`0x01` 为使能，`0x02` 查询使能状态。官方 C++ SDK 的私有 `PrivateOmniHand::SetPowerState(0)` 也对应失能。

这些接口的前提是手部作为独立 O10 设备接入可控总线，知道设备 ID/通道，并且没有 X2 HAL/MC 同时发命令。当前没有证据证明 X2 的机器人接线、仲裁和恢复流程满足这些前提。O10 文档还说明纯 `TORQUE` 模式不支持，位置加 torque 中的 torque 实际是电流 mA；不能用零 effort 伪造卸力。

X2 的 `SetDcuMotorPowerState` 服务目前只确认服务定义存在，`dcu_type=1/2` 的映射、state 数值和作用范围未确认。不能调用未知值。整机断电会影响腿、腰和 MC，不适合在保持站立的机器人上制作手部姿态。

## 6. 上肢轨迹

### 录制

录制读取机器人实际反馈，不是拖动示教。每帧包含：

- 双臂 14 个关节，每侧 7 个；
- 左手 10 个主动轴；
- 右手 10 个主动轴；
- 相对时间戳 `t_ms`；
- 实际位置、速度、effort（只要反馈提供）。

不录腿、腰、头、根部、底盘速度，也不会因点击录制自动卸力或生成姿态。没有已确认的官方操作时，无法声称存在“双手捧出”示教。

### MC CSV

MC runner 每 2 ms 消耗一行，因此 20 Hz 录制不能直接作为 20 Hz 播放。`mc_animation.py` 将轨迹插值到 500 Hz，并只导出：

```text
timeMS, command_pos::<14 arm names>, command_pos::<20 hand names>
```

不补腰、头、腿或根部列，不复制速度和 effort 列。MC 读取 soc0 上的文件，不直接读取 PC2 本地路径；上传会校验 SHA-256。

### 播放状态

`preparing -> playing -> pausing -> paused -> stopping -> completed/error`，停止无法确认时为 `stop_failed`。暂停和停止都以当前反馈姿态生成恒定动画并以 `interrupt=true` 替换，等待状态序列、idle 和稳定姿态反馈。RPC 成功、进度估算或本地任务结束都不能单独证明停止。

## 7. 安全模型

### 软件保护

- 默认启动 `IDLE`，不自动进入 TELEOP。
- 单一控制租约；旧 session 的延迟消息无效。
- 所有速度再限幅，数值必须有限。
- 控制帧或心跳超时进入 `TIMEOUT` 并请求底盘零速。
- 断连、App 后台、退出 TELEOP、软件急停和关闭服务触发活动停止流程。
- 录制/播放期间底盘速度被限制为零；播放期间其他活动互锁。
- MC 停止未确认写入 `mc-motion-unconfirmed.lock`，重启后继续锁住动作。

### 物理边界

软件急停不切断电机电源，不能代替物理急停。HAL 命令没有通用超时失效保护；停止发布不等于机械停止。PC2 看门狗也不能保证终止 MC 已加载的动画。现场必须有人值守物理急停，机器人空载、周围无障碍，任何失控或状态不明时使用物理急停。

## 8. 现场验收

当前现场未就绪，验收工具默认只读。完整顺序：

1. 空载、清空区域、物理急停有人值守。
2. 官方操作准备稳定站立；确认 `Business + STAND_DEFAULT + RUNNING`。
3. 左腕 yaw 最多 0.02 rad、约 3 秒单关节测试。
4. 中途请求保持停止，检查 MC 状态序列、停止延迟、臂反馈和腿/腰/头输出。
5. 通过后再验证暂停/继续、断连、超时、重复播放和 0.25x 全轨迹。
6. 验收人审阅报告和哈希，才填写 `mc-commissioning.json` 并让正常桥接加载。

验收配置要求固件、验收人、报告 SHA-256、MC/配置文件 SHA-256、枚举值、真实状态序列和限制参数。模板位于 `docs/mc_commissioning.example.json`，所有检查默认关闭。不能把离线测试结果填成现场通过。

## 9. 故障排查知识

| 现象 | 原因方向 |
| --- | --- |
| PC2 找不到手柄 | 手柄可能配对在 PC1；先运行输入诊断或改用手机 |
| `aimdk_msgs` 导入失败 | ROS 环境未 source、Python 版本错误或 common/ec 包顺序错误 |
| 速度马上超时 | App 后台、网络隔离、PC2 负载或 WebSocket 断开 |
| 手部目标不保持 | MC/HAL 仲裁或固件模式覆盖；不要提高频率 |
| 录制缺帧 | arm/state 或 hand/state 过期、QoS 不匹配、反馈不完整 |
| 播放能力为 false | 没加载完整验收配置，属于默认保护 |
| 配置哈希失败 | 固件/库/配置变化，必须重新验收 |
| `stop_failed` | 反馈不能证明停止；物理急停值守，保存 trace，明确重试 |
| 轨迹列表为空 | 用户、数据目录或 JSON 文件路径不一致；不要覆盖生产数据 |

## 10. 已知未知项

以下项目不能从当前资料推断：

- `SetDcuMotorPowerState` 的灵巧手 DCU 类型和 state 枚举；
- X2 内部 O10 总线、设备 ID、通道和 SDK 接入关系；
- MC 对手部位置目标的完整仲裁优先级；
- v0.9.7 自定义动画的全部区域过滤、末帧保持和异常恢复语义；
- 灵巧手失能后的安全姿态、反驱阻力、重新使能校准和碰撞风险；
- PC2 崩溃或网络断开时 MC 动画的最终停止行为。

任何未知项都必须通过厂商资料、安装接口或现场受控试验确认，不能用新版文档枚举、O10 独立协议或经验值替代。

## 11. 术语和资料

| 术语 | 含义 |
| --- | --- |
| MC | X2 的全身运动控制模块，负责站立、行走及当前臂/手输出 |
| HAL | 硬件抽象层；接收关节命令并连接 EtherCAT/执行器 |
| soc0 | PC1 上的系统/MC 运行单元 |
| DCU | 设备控制单元；其类型与电源 state 需现场确认 |
| NIMBLE_HANDS | 当前 `GetHandType` 返回值 1 的手部类型解释 |
| ani_path | `SetMcPresetMotion` 的自定义动画文件路径字段 |
| stop_failed | 软件未能用反馈证明停止，后续动作持续互锁 |

主要资料：

- [AimDK X2 官方入口](https://x2-aimdk.agibot.com/zh-cn/latest/about_agibot_X2/index.html)
- [系统状态](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/system_state/index.html)
- [关节控制](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/joint_control.html)
- [预设动作](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/preset_motion.html)
- [O10 官方文档](https://www.agibot.com.cn/DOCS/OS/Omnihand-O10)
- [OmniHand SDK](https://github.com/AgibotTech/agillink_omnihand_sdk)
- 本仓库调查：[X2_V0_9_7_CONTROL_INVESTIGATION.md](X2_V0_9_7_CONTROL_INVESTIGATION.md)
