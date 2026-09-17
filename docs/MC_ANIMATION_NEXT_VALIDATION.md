# MC 动画后续考证计划

本文承接 2026-09-16 的 MC 动画现场测试，供下一次开发会话和现场测试使用。

## 一、本次会话结论

### 已解决的问题

- ROS 环境可用，`aimdk_msgs`、`SetMcPresetMotion` 和 `GetSystemState` 均可发现。
- 只读预检通过：系统为 `Business`，动作是 `STAND_DEFAULT`，MC 状态反馈正常。
- 初次执行失败的原因是 PC2 到 PC1 的 SSH 认证失败，不是 ROS 或 CSV 问题。配置免密 SSH 后上传成功。
- AimDK `ResponseHeader.code=0` 表示成功；`CommonState=400` 表示任务正在运行。旧代码将 `code=1` 当成成功，已修正。
- 上传错误现在会显示 SSH 的实际 stderr。
- ROS 腰部命令订阅必须使用 `BEST_EFFORT` QoS；默认 `RELIABLE` 会收到 incompatible QoS 警告且没有数据。

### 已完成的真实测试

测试目录：`/tmp/x2-animation-attended-test-1`

- 单关节：`left_wrist_yaw_joint`，目标增量 `0.02 rad`。
- CSV：1501 行，2 ms 一个采样点，当前为 14 个臂关节加 20 个手部关节。
- 启动请求：`task_id=39`，`state=400`。
- 保持替换请求：`task_id=40`，`state=400`。
- 最终 MC 状态：`IDLE`。
- MC 状态序列包含 `IDLE/PRE_PLAYING/PLAYING`，说明动画确实被接受并播放。
- 保持替换后左腕反馈波动约 `0.000575 rad`，停止候选有效。
- 腿部、腰部、头部命令在测试期间持续收到，MC 没有停止全身输出。
- 头部命令保持不变。

### 当前未通过项

腰部在播放阶段发生明显变化：

| 测试 | `waist_pitch_joint` 范围 |
| --- | ---: |
| 无动画静态基线，5 秒 | `0.005803 rad` |
| 动画 `PLAYING` 阶段 | `0.030273 rad` |

`waist_roll_joint` 也有一定变化，但幅度较小。静态基线证明 `0.030273 rad` 不是普通站立噪声，当前不能宣称“仅双臂和双手播放、腰部不受影响”。

当前结论是：**MC 动画链路已经打通，但 v0.9.7 播放期间腰部会被明显影响；完整回放和 App 真机能力仍保持关闭。**

## 二、后续考证目标

两个考证必须分别使用新的输出目录，并保留 `report.json`、`trace.json`、CSV 和现场说明。两项测试都要求空载、动作空间无障碍、物理急停有人值守，并先由官方操作准备稳定站立。

### 考证一：延长 MC 动画，确认腰部变化是否只是偏移/过渡

目的：确认腰部变化是否只发生在 `PRE_PLAYING` 或动画刚开始的过渡阶段，长时间播放后是否回到稳定偏移；同时确认腰部是否持续跟随动画状态变化。

建议测试资源：

- 保持与首轮相同的单关节目标和 CSV 格式，避免同时改变变量。
- 将单关节轨迹延长到至少 10 秒；仍按 2 ms 重采样。
- 不在约 2 秒时发送保持替换，先完整观察一段时间。
- 记录 MC 状态、臂反馈、臂命令、腿命令、腰命令和头命令。
- 动画完整结束后再用当前位置保持动画执行停止测试。

需要计算：

- `PRE_PLAYING`、`PLAYING`、`IDLE` 各阶段的腰 pitch/roll 范围。
- 腰部变化是否与动画进度单调相关，还是只在开始时跳变一次。
- 腰部是否在动画结束后回到基线，或保持新的偏移。
- 左腕反馈是否按预期达到更大的轨迹进度。
- 腿部命令是否持续，头部是否保持稳定。

判定：

- 若腰部只在开始过渡时出现一次短暂偏移，之后稳定在接近基线的范围，可记录为 MC 过渡行为，仍需现场确认是否可接受。
- 若腰部在整个 `PLAYING` 阶段持续变化，说明不是简单偏移，考证一失败，进入考证二。
- 若出现不稳定、回位、腿部输出中断或停止不确定，立即使用物理急停并保留证据。

### 考证二：将腰部数据加入 MC 动画

目的：验证在 CSV 中加入腰部通道后，MC 是否能在播放期间维持腰部姿态，同时不破坏站立、腿部输出和停止流程。

实现原则：

- 采集动画开始前的新鲜腰部目标：
  `waist_pitch_joint`、`waist_roll_joint`、`waist_yaw_joint`。
- 首轮只写入恒定腰部保持值，不回放首轮测试中腰部已经发生的变化。
- 每一帧增加：

  ```text
  command_pos::waist_pitch_joint
  command_pos::waist_roll_joint
  command_pos::waist_yaw_joint
  ```

- 禁止用零值填充，禁止使用过期反馈，禁止同时加入未知的腿、根部或其他控制列。
- 记录加入腰部列前后的 CSV 列表、SHA-256 和完整 trace。

必须重新验证：

- 腰部播放阶段范围是否降到接近无动画基线。
- 腿部命令是否持续且机器人保持站立。
- 手臂轨迹是否仍然执行。
- 保持替换是否还能可靠停止并回到 `IDLE`。
- 腰部列是否引入控制竞争、抖动或明显姿态跳变。

风险说明：加入腰部列会使 MC 动画播放器明确输出腰部目标，可能与站立/RL 控制器争抢。不能因为 CSV 能加载就认为该方案安全；必须以空载现场测试结果为准。

## 三、最终决策

### 两项考证都失败

如果延长播放仍显示腰部持续变化，且加入恒定腰部列也不能稳定腰部，接受以下工程结论：

- 腰部动作可能是 v0.9.7 MC 为机器人平衡所必需的行为。
- 不再要求腰部在动画期间保持绝对不变。
- 轨迹能力重新定义为“上肢动画，腰部由 MC 平衡控制”。
- 播放期间仍必须确认腰部变化有界、腿部持续输出、机器人稳定、停止可靠。
- 不得因此自动开放完整 App；需要现场负责人明确接受腰部动作，并更新验收报告和用户文档。

### 任一考证成功

只有在现场证据证明腰部变化可接受、停止可靠、腿部和站立控制未受破坏后，才可更新 commissioning profile。不能仅修改布尔检查项或根据服务响应自动开放。

## 四、测试环境和安全要求

- 测试主机：PC2 `10.0.1.41`，官方 `run` 用户。
- MC/soc0：PC1 `10.0.1.40`，通过已验证的 SSH 认证上传资源。
- `ani_path` 必须是 PC1/soc0 可读路径，不能填写 PC2 本地路径。
- 测试前退出其他遥控和 App TELEOP，机器人空载，动作空间无障碍，物理急停有人值守。
- 不要修改 `area`、`motion`、`interrupt` 来盲试未知组合。
- 不要删除 `mc-motion-unconfirmed.lock` 绕过停止互锁。
- PC2 进程崩溃或 PC2 与 MC 断联时，不能保证 MC 已停止；物理急停是最终保护。

## 五、相关代码和文档

- `src/x2_ps5_teleop/robot/mc_animation.py`：CSV 重采样和列白名单。
- `src/x2_ps5_teleop/robot/mc_animation_probe.py`：现场探针、上传、服务调用和 trace。
- `src/x2_ps5_teleop/robot/mc_playback.py`：验收门控、播放和停止状态机。
- `docs/MC_ANIMATION_COMMISSIONING.md`：完整现场验收流程。
- `docs/X2_V0_9_7_CONTROL_INVESTIGATION.md`：v0.9.7 MC/HAL 调查证据。

## 六、代码化执行方式

探针现在支持两项考证的独立输出目录和参数，默认仍只生成本地证据：

```bash
# 考证一：至少 10 秒，仍只输出双臂和双手
python3 -m x2_ps5_teleop.robot.mc_animation_probe \
  --output /tmp/x2-animation-validation-1 \
  --duration-ms 10000 --stop-mode complete

# 考证二：重新采集新鲜腰部目标，并在每帧加入 3 个恒定腰部列
python3 -m x2_ps5_teleop.robot.mc_animation_probe \
  --output /tmp/x2-animation-validation-2 \
  --duration-ms 10000 --include-waist \
  --stop-mode midway --stop-after 5

# 比较两个目录的 PRE_PLAYING/PLAYING/IDLE 腰部范围
python3 scripts/analyze_validation_results.py \
  /tmp/x2-animation-validation-1 /tmp/x2-animation-validation-2 \
  --output /tmp/x2-animation-commissioning-analysis.json
```

分析 JSON 同时列出 PLAYING 阶段手臂命令/反馈最大误差、保持动画回到
`IDLE` 后至少 300 ms 的臂反馈与命令范围、腿部命令最大真实接收间隔及最大观测
消息年龄、头部命令相对初值偏移、腰部命令相对初值最大绝对偏移。新探针会在每次
采样间隔内累计 DDS 回调的最大消息间隔，避免用约 20 Hz 的 trace 采样周期冒充实际
腿部消息周期。旧 trace 没有稳定窗口时间戳或接收间隔字段时，报告明确标记证据缺失，
不会据此判定通过。该 JSON 只是人工审阅材料，不会生成或修改 commissioning 配置。

若两项考证均未能让腰部保持不变，现场报告可明确声明 `waist_policy: "mc_balanced"`，并同时勾选 `waist_bounded`、填写 `waist_bound_rad`。回放门控仍要求完整停止、状态序列、腿部连续输出等证据；没有该现场报告时不会开放 App。
