# X2 Ultra v0.9.7 上肢控制调查

调查日期：2026-09-16。PC2：10.0.1.41；通过 PC2 只读访问 soc0/PC1：10.0.1.40。没有迁移状态、切换 MC 模式、启动机器人后端、发布控制命令、设置参数、修改厂商配置或电机上下电。

## 最新进展：CSV 输出范围已验证，待现场停止验收

后续已在独立进程调用本机动画库的解析器，验证纯双臂＋双手 CSV：34 列、1501 行，根部列为 0。还实际执行了头/腰/根部缺列分支，均不访问输出上下文。未调用 runner Init/Run，未向机器人发送控制命令。此前“缺列是否允许”的问题已有具体答案：解析器和相关输出分支支持缺列；完整运动效果仍需实测。

另已确认动画每 2 ms 推进一行，`timeMS` 不负责重采样；直接导入 20 Hz 数据会改变播放速度。新增转换器按 2 ms 重采样，并只允许 14 臂＋20 手的命令列。

只读诊断时机器人仍为 `Business / PASSIVE_DEFAULT`。已生成左腕 yaw、0.02 rad、3 秒的单关节测试资源，但尚未执行。候选停止方式是用当前位置的恒定动画替换旧动画，需要现场值守验证延迟和保持效果；不能将文件解析成功算作停止验收通过。详见 [MC 动画验收说明](MC_ANIMATION_COMMISSIONING.md)。

灵巧手 App 的五参数和位置编辑均改为滑条，并按官方 HAL 索引及本机配置标注全部 10 个自由度。拖动只编辑，不自动下发。此次没有更改底盘控制或删除原轨迹。

## 本轮更新：先查 MC 动画，再评估 HAL 回退

本轮仍只读调查机器人，不执行动画、不迁移状态、不停止 MC。灵巧手五参数已按用户确认的旧版可用路径恢复；下文旧轮次的“手部执行拦截”不再代表当前代码。机械臂回放和卸力示教仍未启用。

### 第二条：`SetMcPresetMotion + ani_path`

结论：**有自定义路径的实际实现证据，但尚不能确认满足仅双臂＋双手、暂停/继续/可靠停止的完整回放要求。不能将它说成完全不支持，也不能据服务存在就开放播放。**

- [官方预设动作文档](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/preset_motion.html) 将 `ani_path` 标为“自定义动作地址 (待开放)”。自 v0.8.0 起，`area` 与 `motion` 联合映射动作，不是已确认的关节输出掩码。文档中的新字段 `input_source`、`play_timestamp` 不在当前安装的 srv 中，不能直接套新版请求。
- soc0 当前 `libmc_module_main_service.so` 的 `SetMcPresetMotionCoServiceImpl` 将请求中的路径字符串复制到 `MotionState`，再调用 `mc::api::SetMotionState`。只读反汇编位置 `0xf443c–0xf4444`、`0xf4484`。
- 当前 `libmc_runner_animation_player.so.0.0.0` 的 `AnimationPlayerController::Run` 检查路径是否为空；非空时使用传入路径，空时调用 `FindAniPath(area, motion)`，随后检查文件并调用 `LoadAniFrames`。对应位置 `0x68b24–0x68b28`、`0x68c1c–0x68c24`、`0x68d10`。这是静态执行分支证据，未调用服务验证实际动作。
- 现有 CSV 为 `timeMS`＋`command_pos::<joint_name>`，样例包含腰、头、14 臂、20 手通道。播放器配置也包含 `waist_joints`、`head_joints`、`enable_waist_rl_control: true`。缺列是否允许、是否维持当前腰姿态、双臂之外如何隔离尚未确认；不能随意补零或认为 area=3 就不动腰。
- `interrupt=true` 的文档语义是打断旧动作并执行新动作，不能直接对应 App 的“停止/暂停”。`GetMcPresetMotionState` 只区分执行中和已完成，不能确认中断、失败、暂停或保持。没有据此接入自动执行，也没有以切换全身模式来伪装停止。
- 动画在 MC 主机 soc0 读取文件，未来即使接入，PC2 的 JSON 路径也不能直接作为 `ani_path` 使用。还需要已确认格式转换、soc0 资源路径、输出范围和可验收的停止协议。

本轮检查二进制 SHA-256（供相同版本复核）：

```text
libmc_module_main_service.so
  a708839613a7bd4da70683e3ade185d5c5baec7bd5eeb29b58b3d5ca66d093a4
libmc_runner_animation_player.so.0.0.0
  cb71131a23039d20af2e346e004064a3a780aed7cd3000dfbec8705643e6b477
```

### 第一条：HAL 手臂回退

实际 `/aima/hal/joint/arm/command` 存在，现有代码能构造其 `JointCommandArray`。但运行配置仍启用 MC 的 500 Hz 手臂输出，同时负责腿部站立；未找到独立停止手臂输出、移交控制权并恢复的公开服务/参数。官方底层控制要求停用原生运控，与本任务保留腿部 MC 的约束冲突。官方还说明 HAL 关节目标没有超时失效保护，停止发布并不等于停止机械运动。

因此不能在 Business 下仅删除 `_require_develop_mc()` 或增加一个强制开关作为回退实现。本轮保留手臂保护，不改厂商配置、不触碰腿腰。若厂商确认 v0.9.7 的动画输出范围与停止协议，优先接入 MC 动画；若确认只释放双臂的控制权协议，再接入 HAL 回退。两条路径都不要求升级，但当前证据尚不足以完成真机回放。

### 手指恢复的依据与范围

先前将手指与手臂一起封禁过度。本轮依据用户明确确认“旧版参数控制在本机可用”，恢复 `hand_target` 与原五参数编辑器；位置、速度、加速度、减速度、effort 均按原协议发送，不将所有字段都宣称为已验证的硬件能力。

- `App → BridgeController → HandCommandArray → /aima/hal/joint/hand/command → 现有 HAL`，不另启独立 SDK，不停 MC。
- 旧版参数沿用左手前三槽取反；新位置预设直接发送实际反馈符号，防止二次镜像。每次仅填所选手的 10 槽，另一手数组为空。
- 保留 TELEOP、控制租约、输入校验和播放/暂停期间的互锁；位置预设仍可保存、改名和删除，松手预设保留交接确认。
- MC 在不同模式下会不会覆盖目标仍需现场核验。恢复可用路径不等于确认所有模式的控制权仲裁；出现回弹或抖动时不应提高发送频率抢占。
- 灵巧手独立卸力仍未确认，未调用 `SetDcuMotorPowerState`。五参数中的 effort=0 不能当卸力命令。

本地 Python 测试 57 passed，App TypeScript 检查通过。新增测试覆盖左右手参数透传、旧版左手取反、原始反馈位置不取反、另一手为空和无效输入拒绝。未进行实机运动测试，未宣称暂停/停止已验收。

本轮已将代码和文档通过 SSH 文件传输同步至 PC2 `/home/run/zhiyuan111`，PC2 测试同样 57 passed。备份：`/home/run/x2-backups/hand-restore-20260916-152741`。本轮同步前后轨迹 SHA-256 均为 `f5ddc717800d473a02d3ab1a3bdaa252a4aadbbe0ac7a7cd86dcbd9d995ca5a2`；它与前轮哈希不同是同步前已有的新数据，本次未覆盖。PC2 已运行的桥接未重启，新功能需现场退出 TELEOP、重启桥接并刷新/更新 App 后生效。两端原有的启动脚本修改均未触碰。

## 前轮结论与现场记录

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

OmniHand SDK 的 `doc/en/API_CPP_O10.md` 表明 O10 不支持通过 `SetControlMode` 切模式，纯 TORQUE 模式不支持；混合控制的 torque 字段实际上是 mA。不能把零电流阈值、零 effort 或私有头文件的 `SetPowerState` 枚举当作 X2 ROS 服务卸力协议。O10 C++ 头文件中的 `PrivateOmniHand::SetPowerState(0)` 是直连 StreamCmd/CAN-FD 的 O10 设备接口；O10 Python 文档没有把该私有接口列为公开示教 API。它只有在确认灵巧手独立接到可控的 O10 总线、确认左右手设备 ID/通道、并停止 X2 HAL/MC 对该手的发布后才有意义。不能在机器人 HAL 同时运行时另接 SDK 抢控制，也不能把 O10 的 CAN 帧直接发到 X2 未确认的总线。

因此当前可行性分为三档：

1. **首选：厂商确认的 X2 手部专用失能/示教模式。** 需要书面确认 `SetDcuMotorPowerState` 的 `dcu_type` 哪个对应灵巧手、`state` 的定义、作用范围（只手还是整组 DCU）、恢复流程，以及 MC/HAL 仲裁。确认后再实现受保护的只手操作，并用反馈录制预设。
2. **条件可行：手部独立 O10 SDK/CAN-FD。** 需要确认 X2 的手是原生 O10 设备、物理总线和设备 ID 可独立访问，且厂商允许暂停当前 HAL/MC 手部输出。使用 SDK 的 C++ `SetPowerState(0)` 或官方上位机失能功能后，手动摆姿态，再只读采集 10 个手部反馈；恢复前必须按厂商流程重新使能和校验。当前没有这些接线、仲裁和恢复证据，不能部署。
3. **不建议：整机断电。** 会同时影响腿、腰、MC 和安全状态，不能用于在保持站立的 X2 上制作手部姿态；也不能把 `effort=0`、发送零速度或停止发布命令当成卸力。

在第 1 或第 2 档得到现场确认前，使用现有“反馈位置与预设”只能记录机器人已经执行过的手部动作；录制不会卸力，也不能自动生成“双手捧出”。严禁在手指仍上电或 HAL/MC 仍争抢控制时强掰。若厂商确认了仅手部失能链路，制作流程应是：空载且急停有人值守 → 停止手部控制竞争 → 仅失能左/右手 → 小幅手动摆姿态 → 读取新鲜反馈并保存预设 → 恢复使能 → 单手空载位置复核。每一步都要有可观测的状态反馈，失败时使用物理急停，不靠删除互锁文件恢复。

结论：**目前没有可直接执行的 X2 灵巧手卸力命令。已找到 O10 单机 `SetPowerState(0)` 的公开 C++ 声明和官方失能帧，但它不是已确认的 X2 HAL 接口；需要厂商确认或现场只读调查接线/仲裁后才能实现。**

## 改动和验收边界

以下为首轮调查时的记录。后续状态录制、手部预设及能力拦截实现，以 [上半身轨迹说明](UPPER_BODY_TRAJECTORY.md)、[App 手册](APP_USER_GUIDE.md) 和 [部署手册](PC2_DEPLOYMENT.md) 为准。

- `motion.py`：只修正状态检查失败时的提示，保留 `_require_develop_mc()` 的原放行条件；没有新增控制命令。
- `UPPER_BODY_TRAJECTORY.md`：撤回直接切换 Develop_MC 的建议，明确真机能力与停止语义未验收。
- 本文：保存官方来源、现场配置路径、迁移表与未确认项。
- PC2 仓库代码未部署修改；未重启桥接。工作区已有的 `scripts/start_mobile_bridge.sh` 改动未触碰。
- 本地和 PC2 的原有测试各 30 passed；本地 App `tsc --noEmit` 通过。它们不证明控制权移交或机械停止有效。
- 尚未执行单关节、空载 20 Hz 3–5 秒、停止验证和 0.25x 完整轨迹测试：控制链路尚不满足前提。
- 轨迹文件调查前后 SHA-256 相同：`0f09b5fb76a25de6066884aec8c009a308a5bfacd741dad77a3beaeb9ad6d665`。没有删除、覆盖或转换已有轨迹。

需进一步确认：官方控制权仲裁、仅上肢示教、灵巧手卸力/恢复、关节顺序与手部符号/单位、暂停/停止/掉线保持，以及手指抓握在官方人工行走期间是否稳定（用户不要求保持手臂姿态）。确认后再依次进行现场急停值守下的小幅单关节、空载短时、停止和慢速整轨迹验收。

## 后续实现与补充证据

本次进一步读取官方末端执行器文档，原文要求使用底层手命令前在 PC1 执行 `aima em stop-app mc`，以禁止原生运控接管手部。没有执行该命令；这一前提与保留官方行走冲突，前轮因此封禁了手部发送。本轮按用户确认的本机旧版可用路径恢复，见本文开头；仍不停止 MC。

实机 `SM_wave_left_hand.csv` 的头行为 `timeMS` 加 `command_pos::<joint_name>`，包含腰、头、14 臂关节及 20 手部通道。只确认了样本结构，尚未确认缺列、区域过滤、末帧保持与停止语义，没有生成或上传可执行动画。

额外进行了 3 秒只读状态订阅，收到完整 14＋10＋10 反馈。手部关节名均为空，因此采样保留数组顺序；实际位置出现约 `1.00003 rad`，原 UI 的 ±1 输入边界不适合作为硬件限位依据。新位置编辑器采用 ±π 的应用边界并明确不等价于硬件限位，不对反馈做隐式裁剪或左手镜像。

在现有仓库实现了默认只读状态录制、位置预设独立持久化、交接确认、能力展示与真机执行拦截、Mock 重复播放，以及录制/播放期间的心跳看门狗。`单手携带` 快捷入口不再触发手臂轨迹。未接入独立 OmniHand SDK 或 MC 动画执行，不声称完成真机颁奖控制。

本地 49 tests passed；PC2 临时目录运行同版代码 49 tests passed；App TypeScript 检查通过。PC2 的第一轮临时测试缺少测试用配置文件，补齐临时目录中的 config 并更正工作目录后通过。原项目未覆盖、原桥接未重启；已有轨迹 SHA-256 仍为前述值。操作与部署文档已同步更新。
