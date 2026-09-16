# 上半身轨迹

桥接服务的轨迹录制只采集真实的上半身状态：左右机械臂和左右灵巧手。底盘速度、腿部和腰部不会写入轨迹。

录制消息：

```json
{"type":"trajectory_record_start","name":"双手递出","sample_rate_hz":20}
```

`sample_rate_hz` 支持 `1` 到 `100` Hz，默认 `20` Hz。状态帧使用相对录制开始时间的 `t_ms`，机械臂关节按名称保存实际位置、速度和力矩，灵巧手保存实际位置、速度和力矩。

播放消息：

```json
{"type":"trajectory_play","name":"双手递出","speed":0.5}
```

播放速度支持 `0.01` 到 `4` 倍。播放期间可发送同一类型的 `command`：`pause`、`resume` 或 `stop`。每个 `upper_body` 帧由桥接层统一发送机械臂和双手目标；不会发布腿部、腰部命令。

轨迹默认持久化到 `~/.x2_ps5_teleop/trajectories.json`。移动端轨迹页提供采样频率、播放速度、暂停、继续、停止以及五个独立颁奖动作入口。五个入口要求轨迹名称分别为：`单手抓取`、`单手携带`、`双手接持`、`双手递出`、`收回双手`。

当前实现没有调用全身 `DAMPING_DEFAULT` 或 `ZERO_TORQUE_DEFAULT`。上半身单独卸力仍需现场确认 AimDK 控制器语义后再启用。
