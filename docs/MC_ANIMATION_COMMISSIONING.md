# v0.9.7 MC 动画回放验收

本轮目标是使用 `SetMcPresetMotion + ani_path` 接入现有轨迹。已确认自定义 CSV 加载和输出列选择，已完成独立 MC 后端、控制器、App 和离线模拟集成。**尚未完成运动验收，App 真机回放默认不可用；当前现场未就绪，本文所有 execute 命令均不得现在执行。**

## 已确认的固件行为

现场二进制及其哈希见 [调查报告](X2_V0_9_7_CONTROL_INVESTIGATION.md)。本轮在 soc0 的独立进程加载相同动画库，只调用文件解析及缺列分支；没有创建 MC runner、调用 Init/Run、初始化 ROS 或发布控制命令，也没有注入或修改正在运行的 MC。

- 只导出 14 个臂关节＋20 个手部主动轴的 `command_pos::` 列即可解析。实际导出文件结果：`joint_columns=34 root_columns=0`，每列 1501 行，手部符号保持原值。
- `FillJointCommands` 查询列名，找不到则跳过，适用于头部；`FillWaistDesire` 对腰部缺列也直接跳过；`FillRootDesire` 在根部数据为空时直接返回。独立进程已执行这些缺列分支，不访问输出上下文。此结果不等于物理站立/停止已验收。
- `LoadAniFrames` 不根据 `timeMS` 插值。带 0/50/100 ms 的三行样本加载后仍为三行；`PlayAnimation` 每次推进一行，当前 `action_setting.yaml` 的动画周期为 2 ms。必须先重采样到 500 Hz，不能让 20 Hz 录制以 500 Hz 快放。
- `preplaying_tick=500`，启动包含过渡阶段，服务响应不代表立即开始原轨迹计时。
- `interrupt=true` 替换动作时会在新文件前 25% 中寻找邻近帧，随后做过渡。因此用完整轨迹替换不能保证从第零帧开始，也不能直接作为“继续”。
- 当前没有已验证的即时停止接口。候选是将当前位置生成恒定姿态 CSV，以 `interrupt=true` 替换旧动画，再用臂命令和实际反馈测量停止延迟。`motion=0` 不作为停止协议使用；不能假定它保持当前位置。

转换器：`src/x2_ps5_teleop/robot/mc_animation.py`。它只生成资源，不调用任何机器人控制接口；包含严格的关节白名单、有限数检查、同时间戳冲突检测、速度重采样和资源大小限制。不会读写生产轨迹文件。

## 首轮实机测试

诊断程序：`src/x2_ps5_teleop/robot/mc_animation_probe.py`。不构造遥操作机器人后端、不注册输入源、不切系统状态、不切运动模式、不启动或停止厂商服务。

**执行前由现场人员确认：**空载、动作空间无障碍、物理急停有人值守；通过官方操作准备稳定站立，退出其他遥控和 App TELEOP。程序只接受 `Business + STAND_DEFAULT + RUNNING` 且无正在执行的动画。它不会自动把被动模式切到站立。

测试内容：

1. 读取新鲜的臂和手状态，生成 20 Hz、3 秒的单关节输入，左腕 yaw 最多移动 0.02 rad（约 1.15°）；其他臂、手目标为测得值。
2. 重采样为 1501 行、2 ms 间隔的 CSV，仅含双臂与双手列。上传 soc0 的唯一临时路径并核对 SHA-256。
3. 发送 MC 动画请求，同时以约 20 Hz 留存臂反馈以及臂、腿、腰、头命令的观测值和接收时间。
4. 请求返回约 2 秒后，重新采集实际姿态并生成静止动画，发送替换请求；再观察 2 秒，检查轨迹是否停止推进以及是否发生回位。
5. 检查 `trace.json` 和响应，计算停止延迟、停止后的目标/反馈波动，确认腿部输出持续，核实腰、头命令无动画引入的变化。若失败，报告保留错误，不自动开放回放。

异常时程序尝试发送当前位置的静止动画，但这本身仍是待验收的停止链路；不能代替现场物理急停。

## 操作命令

按 [PC2 部署说明](PC2_DEPLOYMENT.md) 设置 ROS 环境，工作目录 `/home/run/zhiyuan111`，`PYTHONPATH` 包含 `$PWD/src`。首次诊断建议使用系统 Python 3.10。每次使用一个新的输出目录。

默认只读取并生成本地文件，不上传、不发动画请求：

```bash
python3 -m x2_ps5_teleop.robot.mc_animation_probe \
  --output /tmp/x2-animation-preflight-new
```

现场明确确认后才执行：

```bash
python3 -m x2_ps5_teleop.robot.mc_animation_probe \
  --output /tmp/x2-animation-attended-test-new \
  --ssh-host run@10.0.1.40 \
  --execute --attended-estop --unloaded
```

SSH 必须事先具备已确认的主机密钥和可用认证。工具使用 `BatchMode=yes`，不会存储口令或禁用主机校验。如果现场正在使用已认证的 SSH ControlMaster，可增加 `--ssh-control-path <现有连接的套接字>`；临时套接字不能作为正式部署的长期认证方式。

输出：`trajectory.json`（单关节实际起点的录制格式资源）、`probe.csv`、`hold-preview.csv`、执行时的 `hold.csv`、`report.json`、`trace.json`。`executed:false` 表示未调用控制服务；`commissioned:false` 不会因生成了文件或服务返回成功而自动变为 true。

## 开放完整回放前

已接入 MC 整条资源播放，等待首轮停止测试；MC 路径使用实际站立/动画状态校验，保留 HAL 的 `_require_develop_mc()` 保护。暂停采用已验收的停止保持操作，继续从保留进度生成剩余片段，明确过渡时间与估算进度。软件停止、断连、超时、进程退出均需走该停止链路；错误不能显示成已停止。

最后验证 0.25x 完整轨迹、重复播放、暂停/继续、停止和断线，检查期间不发布腿、腰或底盘轨迹。MC 会自行播放已加载文件，PC2 进程崩溃或 PC2↔MC 通信丢失时，仅靠 PC2 的看门狗不能终止动画；该故障边界也需现场验收并明确处理。


## 分阶段验收及开放配置

不能先把所有检查写为 true 再做测试。配置模板为 [mc_commissioning.example.json](mc_commissioning.example.json)，默认检查均 false、状态序列为空、报告哈希未填，因此不能开放桥接。模板枚举值已于 2026-09-16 只读核对 PC2 `common/share/aimdk_msgs/msg/McPlayerState.msg`；**枚举定义不等于已观测到的状态序列**。

1. 空载、急停有人值守、用官方操作稳定站立后，先用前述 probe 完成左腕 yaw ≤0.02 rad / 3 秒及中途保持停止。记录真实状态序列、臂手反馈和腿腰头输出，测量停止延迟；不通过就停止调查，不能开放 App。
2. 由验收人审阅证据，填写模板的报告路径/SHA-256、验收人、soc0 两个库与三个配置文件 SHA-256、实际观测的 `play_sequence` 和 `hold_sequence`（小写、连续重复状态合并、以 idle 结束），以及测量支持的限制参数。先只勾选实际通过的 `single_joint`、`hold_stop`、`legs_waist_unchanged`、`status_sequence`。
3. 此时正常桥接仍会拒绝配置。使用专门的 **单次现场 trial** 验证剩余流程；它只接受前四项首轮证据，不能开启 App、不能切模式、不会注册新的官方输入源或发布 HAL 目标。验收时退出其他控制器和桥接，防止争抢；每次都手动准备对应起始姿态。输出目录必须新建。

trial 默认仅从本地轨迹文件生成 0.25x CSV 和 `execution_requested:false` 报告，不初始化 ROS、不上传、不调用 MC：

```bash
python3 -m x2_ps5_teleop.robot.mc_playback_trial \
  --trajectory-file /tmp/x2-animation-attended-test-new/trajectory.json \
  --name single_joint --output /tmp/x2-trial-preview-new
```

**以下仅在现场准备完成并审阅首轮停止证据后执行：**

```bash
python3 -m x2_ps5_teleop.robot.mc_playback_trial \
  --trajectory-file /tmp/x2-animation-attended-test-new/trajectory.json \
  --name single_joint --output /tmp/x2-trial-stop-new \
  --profile /home/run/.x2_ps5_teleop/mc-commissioning.json \
  --mode stop --after 1 --execute --attended-estop --unloaded
```

`--mode` 可选 `stop`、`pause-resume`、`disconnect`、`timeout`、`complete`；中途操作按观测到 playing 后 `--after` 秒触发。`disconnect` 和 `timeout` 使用真实 BridgeController 的租约撤销/看门狗路径、真实 MC 传输，控制适配器不发送其他机器人命令；这不等于测试了物理网络故障。每次输出独立的 `report.json`、`trace.json`、`preview.csv`，不会自动勾选任何验收项。暂停继续如果起点不匹配会拒绝，不允许放宽到不合理容差来让测试通过。重复播放需每次先用已确认操作恢复起点。完整轨迹用生产轨迹文件只读加载、`--mode complete`，速度固定 0.25x，报告限制仍生效。不能将包含多余模式/事件的旧轨迹当成上肢 CSV。

4. 审阅试验的暂停继续、重复播放、断连处理及 0.25x 全轨迹证据后，才将相应 `pause_resume`、`repeat_playback`、`disconnect_stop`、`full_clip_025x` 置 true，并更新现场报告和哈希。仍须明确 PC2 崩溃/物理断网无法靠 PC2 看门狗停止 MC 的边界和现场处置。桥接实机启用后的第一次操作继续有人值守，核验真实 App/网络断连。
5. 完整报告通过后，维护人员在 [部署手册](PC2_DEPLOYMENT.md) 的正常 X2 启动命令后增加 `--mc-commissioning-profile /home/run/.x2_ps5_teleop/mc-commissioning.json`。未提供配置时保持关闭；无 force 开关。当前阶段不创建通过的配置，也不重启桥接。

参数都是验收上限，不是硬件规格：`max_speed ≤0.25`、`max_joint_speed_rad_s ≤0.4`、`max_clip_seconds ≤60`、`start_tolerance_rad ≤0.05`、`hold_tolerance_rad ≤0.03`、`stable_tolerance_rad ≤0.003`、`hold_snapshot_max_age_s ≤0.25`、`transition_timeout_s ≤10`。均须大于零，并依据实测选择更严格值。保持动画上传超过姿态有效期或偏差超限会进入 stop_failed，不会用陈旧姿态强制回拉；因此网络时延也必须验收。

活动状态：preparing / playing / pausing / paused / stopping / stop_failed / completed / error。MC 过渡导致进度是估算；只有完整序列、末端反馈匹配才显示完成。停止必须观察替换序列、idle、位置接近保持目标，并连续 300 ms 稳定。服务返回成功不足以确认停止。停止失败不会自动重复发命令，需用户明确“重试停止”。即使清除软件急停锁存或重新连接，其他动作仍被互锁。

`~/.x2_ps5_teleop/mc-motion-unconfirmed.lock` 表示上次执行没有可靠结束。禁止手工删除以解除动作互锁；保留证据，在现场值守下使用原报告配置重试停止。若状态/网络条件使软件无法确认，使用现场物理急停并继续诊断。正常关闭服务会等待停止处理，失败时以错误退出并保留文件；强杀或失联没有停止保证。
