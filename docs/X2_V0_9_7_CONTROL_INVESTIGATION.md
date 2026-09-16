# X2 Ultra v0.9.7 上肢控制调查

调查日期：2026-09-16。PC2：10.0.1.41；通过 PC2 只读访问 soc0/PC1：10.0.1.40。没有迁移状态、切换 MC 模式、启动机器人后端、发布控制命令、设置参数、修改厂商配置或电机上下电。

## 结论

阻塞来自固件能力与最新文档不匹配，而非 ROS 服务名拼错。当前运行的系统状态机确实没有 `Develop_MC`；官方更新记录将开发者模式列为 v1.0.0 新增，将 `/mc/upper_body_command` 列为 v1.1.0 新增。即使将来具备 `Develop_MC`，该模式停用的是全身原生运控，不满足保留腿部站立控制的要求。

当前未确认能安全接入现有 14 关节＋双手轨迹的控制链路，因此不放宽 `_require_develop_mc()`。本次仅修正拒绝提示，不再提示用户直接切换开发状态。现有检查本身不能作为未来固件上的完整安全保证。

## 官方资料

- [X2 官方入口](https://x2-aimdk.agibot.com/zh-cn/latest/about_agibot_X2/index.html)
- [系统模式](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/system_state/index.html)：`Develop_MC` 停用原生运动控制，开放全身 EtherCAT 控制。
- [版本记录](https://x2-aimdk.agibot.com/zh-cn/latest/changelog.html)：v1.0.0 开发者模式；v1.1.0 上肢控制。
- [MC 上肢接口](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/upper_body_control.html)：`/mc/upper_body_command`、`UpperBodyCommandArray`、`UPPERBODY_REMOTE_SPLIT`；这是新版候选链路，不代表当前固件可用。
- [HAL 关节接口](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/joint_control.html)：关节命令没有超时/失效保护；停止发送不等于安全停止。
- [X2 使用指南入口](https://www.agibot.com.cn/filepage/291.html)：页面明确指向最新版固件指南，不能替代 v0.9.7 的现场配置。
- [OmniHand 官方入口](https://www.agibot.com.cn/DOCS/OS/Omnihand-O10)及其链接的 [SDK](https://github.com/AgibotTech/agillink_omnihand_sdk)。本次读取 SDK 提交 `026740d9fdd8ba32b0605fa702a992b322076f1b`。

## 运行版本与状态机

PC2 `/agibot/software/metadata.yaml`：`version: v0.9.7`，`product: lx2501_3_t2d5`，接口版本 `v0.9.0.7`。

只读服务响应：

- `GetSystemState`：`cur_state='Business'`、`curr_status.value=1`（安装的 `SystemStatus.IN_READY`）。
- `GetMcAction`：`action_desc='PASSIVE_DEFAULT'`、`status.value=100`、`current_action.value=0`。系统 Business 不等于机器人已站稳；数值还与安装消息中的 `PASSIVE_DEFAULT=1` 不一致，不能凭枚举推断运行能力。
- `GetHandType`：`left_hands_type.value=1`、`right_hands_type.value=1`。

`/soc0_sm2253` 提供 `GetSystemState` 和 `MigrateSystemState`，没有枚举系统模式的 ROS 服务。参数列表只有 ROS 通用参数；状态机配置来自 soc0 进程命令行：

```text
./aim_sm_app --cfg_file_path=./cfg/lx2501_3_t2d5.yaml
/agibot/software/sm/bin/cfg/lx2501_3_t2d5.yaml
```

该文件的 `system_modes` 和 `next_mode_list` 如下（去掉重复条目）：

| 状态 | 配置中的下一状态 |
| --- | --- |
| Startup | Ready |
| Ready | Business、EStop、ProductionTest |
| Business | OTA、EStop、ProductionTest、Calibration、AgeingTest、Poweroff |
| EStop | OTA、ProductionTest、Calibration、AgeingTest、Poweroff |
| OTA | Ready、EStop |
| ProductionTest | Ready、EStop、Calibration、Business、AgeingTest、Poweroff |
| Calibration | Ready、EStop、Business、ProductionTest、AgeingTest、Poweroff |
| AgeingTest | Ready、EStop、Business、ProductionTest、Calibration、Poweroff |
| Poweroff | 无 |

这是配置证据，不是状态迁移操作建议；没有试切上述状态。`Auto_transToReady` 和 `Auto_transToManual` 为 true。没有任何 `Develop_*`。`Motion` 功能组只有 `soc0_mc`；Business/Ready 等激活整个 Motion 组，没有手臂单独开发状态。生产、标定、OTA 状态不能作为保留站立控制的替代品。

## 实际 MC / HAL 控制架构

soc0 的 MC 使用 AimRT，配置开启 ROS 2 plugin、channel 和 RPC 后端。ROS 节点 `/mc_ros2_node2373` 实际加载：

```text
/agibot/software/mc_param/robot/lx2501_3_t2d5/mc.yaml
```

- 人工速度输入：`/aima/mc/locomotion/velocity` → MC → HAL → EtherCAT。
- MC 配置手臂命令发布 500 Hz、灵巧手命令发布 50 Hz。它同时负责腿、腰等输出。
- `ros2 topic info -v` 确认手臂/手命令的发布者均为 MC；接收者有 `/hal_ethercat_x21373` 和数据记录节点 `/soc0_drp_ros2_node1389`。本次未测量实际发送频率。
- MC 订阅速度、体态、VR 数据和状态反馈；没有 `/mc/upper_body_command` 订阅。PC2 common 消息包也没有 `UpperBodyCommandArray`。
- ROS action list 为空。`SetMcAction` 是 ROS service；MC 内部 action/runner 不等于 ROS Action server。
- 没有发现仅暂停 MC 手臂发布的公开服务/参数。配置中存在 `PublisherManager.enable`，但它是启动配置，不能据此宣称存在运行时移交协议，更不能通过改配置实现接管。

| 现有接口/模式 | 已确认的范围及限制 |
| --- | --- |
| SetMcAction / JOINT_DEFAULT | 安装消息定义为位控站立；实际 runner 包含上下肢 planner。不是任意关节流输入接口 |
| SetMcPresetMotion | 存在，参数有 McControlArea、McPresetMotion、interrupt、ani_path；控制区域是动作请求参数，不是 HAL 控制权开关 |
| McControlArea | 安装消息只有 NONE、LEFT_HAND、RIGHT_HAND、HEAD、WAIST；没有“暂停手臂 MC”字段 |
| SetMcMotion / RegisterCustomMotion | 存在；SetMcMotion 注释明确为全身动作。不能将现有 JSON 轨迹直接当资源传入 |
| VR_REMOTE_CONTROLLER | 配置同时运行 vr_remote_controller 和 rl_cpgtelecon；MC 接收 `/aima/teleop_bridge/vr_data`。这是 VR 手柄状态协议，不是 14 关节轨迹输入 |
| MANIPULATE_DEFAULT | 名字出现在 action_state.yaml，但 action_setting.yaml 中对应 runner 整段被注释，不能据名字判定支持 |
| HAL arm/hand command | 实际存在，但当前已有 MC 发布，叠加用户发布有控制竞争 |

`STAND_DEFAULT` 配置有 `rl_cpgtelecon` 和 `animation_player`，说明旧版具备 MC 管理的站立＋预设动画架构。其自定义文件格式、手部映射、播放速度、暂停/停止/保持和行走时姿态保持未完成官方确认，不能把“预设动作存在”当作本需求已实现。

后续应由厂商提供此固件的受支持上肢轨迹/示教协议，或确认可升级到有 MC 上肢接口的版本，再按实机接口适配。升级不在本次操作中。新版位置目标接口也不能自动解决拖动示教卸力问题。

## 灵巧手卸力

实机服务 `/aimdk_5Fmsgs/srv/SetDcuMotorPowerState` 由 `/hal_ethercat_x21373` 提供，安装定义为：

```text
CommonRequest request
# 从站序号 1 2
uint8 dcu_type
# 对应状态
uint8 state
---
CommonResponse reponse
uint8 ret
```

HAL 自带 CHANGELOG v0.2.2 只写 `support dcu motor power state set, by ros2 service`。能够确认它属于 DCU 电机电源状态服务；不能确认 1/2 分别覆盖哪些电机、state 数值、是否影响腿腰或是否包含灵巧手。没有调用它。

[O10 官方产品说明书（2026-08-02 版）](https://www.agibot.com.cn/file/ueditor/php/upload/file/20260827/1787811897597743.pdf) 的 PDF 第 19、21、23 页明确给出 CAN-FD、USB/串口协议及设备使能表：命令 `0x01` 的数据 `00` 为失能、`01` 为使能、`02` 为校准模式；命令 `0x02` 查询使能状态。这些是 **O10 单机协议值**，不是 `SetDcuMotorPowerState.state` 的定义；不能跨协议套用。没有发送这些命令。

OmniHand SDK 的 `doc/en/API_CPP_O10.md` 表明 O10 不支持通过 `SetControlMode` 切模式，纯 TORQUE 模式不支持；混合控制的 torque 字段实际上是 mA。不能把零电流阈值、零 effort 或私有头文件的 `SetPowerState` 枚举当作 X2 ROS 服务卸力协议。O10 单机 SDK 与机器人 HAL 的接入关系和固件兼容性未确认。结论：**单机失能协议已找到；X2 上灵巧手独立卸力/拖动示教链路仍未确认**。

## 改动和验收边界

以下为首轮调查时的记录。后续状态录制、手部预设及能力拦截实现，以 [上半身轨迹说明](UPPER_BODY_TRAJECTORY.md)、[App 手册](APP_USER_GUIDE.md) 和 [部署手册](PC2_DEPLOYMENT.md) 为准。

- `motion.py`：只修正状态检查失败时的提示，保留 `_require_develop_mc()` 的原放行条件；没有新增控制命令。
- `UPPER_BODY_TRAJECTORY.md`：撤回直接切换 Develop_MC 的建议，明确真机能力与停止语义未验收。
- 本文：保存官方来源、现场配置路径、迁移表与未确认项。
- PC2 仓库代码未部署修改；未重启桥接。工作区已有的 `scripts/start_mobile_bridge.sh` 改动未触碰。
- 本地和 PC2 的原有测试各 30 passed；本地 App `tsc --noEmit` 通过。它们不证明控制权移交或机械停止有效。
- 尚未执行单关节、空载 20 Hz 3–5 秒、停止验证和 0.25x 完整轨迹测试：控制链路尚不满足前提。
- 轨迹文件调查前后 SHA-256 相同：`0f09b5fb76a25de6066884aec8c009a308a5bfacd741dad77a3beaeb9ad6d665`。没有删除、覆盖或转换已有轨迹。

需进一步确认：官方控制权仲裁、仅上肢示教、灵巧手卸力/恢复、关节顺序与手部符号/单位、暂停/停止/掉线保持，以及单手携带时人工行走是否维持该姿态。确认后再依次进行现场急停值守下的小幅单关节、空载短时、停止和慢速整轨迹验收。

## 后续实现与补充证据

本次进一步读取官方末端执行器文档，原文要求使用底层手命令前在 PC1 执行 `aima em stop-app mc`，以禁止原生运控接管手部。没有执行该命令；这一前提与保留官方行走冲突，故当前 X2 后端禁止旧、新手部命令直接写 HAL。

实机 `SM_wave_left_hand.csv` 的头行为 `timeMS` 加 `command_pos::<joint_name>`，包含腰、头、14 臂关节及 20 手部通道。只确认了样本结构，尚未确认缺列、区域过滤、末帧保持与停止语义，没有生成或上传可执行动画。

额外进行了 3 秒只读状态订阅，收到完整 14＋10＋10 反馈。手部关节名均为空，因此采样保留数组顺序；实际位置出现约 `1.00003 rad`，原 UI 的 ±1 输入边界不适合作为硬件限位依据。新位置编辑器采用 ±π 的应用边界并明确不等价于硬件限位，不对反馈做隐式裁剪或左手镜像。

在现有仓库实现了默认只读状态录制、位置预设独立持久化、交接确认、能力展示与真机执行拦截、Mock 重复播放，以及录制/播放期间的心跳看门狗。`单手携带` 快捷入口不再触发手臂轨迹。未接入独立 OmniHand SDK 或 MC 动画执行，不声称完成真机颁奖控制。

本地 49 tests passed；PC2 临时目录运行同版代码 49 tests passed；App TypeScript 检查通过。PC2 的第一轮临时测试缺少测试用配置文件，补齐临时目录中的 config 并更正工作目录后通过。原项目未覆盖、原桥接未重启；已有轨迹 SHA-256 仍为前述值。操作与部署文档已同步更新。
