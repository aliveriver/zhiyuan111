# 固定语音与 AP 使用记录

适用固件：`release-lx2501_3_t2d5-soc0-v0.9.7`。

本次只完成本地代码和配置，未连接真机，未通过 SSH 检查实际 ROS 服务。因此 `PlayTts`、`PlayAudioFile` 是否已安装在这台 v0.9.7 设备上，仍需现场启动 X2 Bridge 后确认。

## 语音按钮

移动控制页的“固定语音”按钮由 `config/voice_presets.json` 动态生成。配置中有几条 `presets`，页面就显示几个按钮；修改配置后重启 PC2 Bridge，重新连接 APP 即可看到新列表。

当前配置包含两个固定音频按钮：

- `ten_years_review`：`十年赛事回顾.wav`。
- `ten_years_summary`：`总结十年赛事.wav`。

添加多个固定音频时，每个文件增加一条：

```json
{
  "id": "award_intro",
  "label": "颁奖开场",
  "mode": "file",
  "file_path": "/var/tmp/x2_ps5_teleop",
  "file_name": "award_intro.wav",
  "priority": 6,
  "priority_weight": 0
}
```

`file_path` 和 `file_name` 按官方要求填写。音频文件必须放在交互计算单元 PC3；当前两个文件配置到官方示例使用的 `/agibot/data/var/hal_audio/file`。官方支持 PCM 编码 WAV 或原始 PCM，不支持 MP3；当前文件为 16-bit、单声道、32 kHz，系统会按官方链路重采样。

TTS 和固定音频都要求 APP 先进入 `TELEOP`。发送语音请求不会调用停车，也不会禁用移动页面的摇杆；移动速度仍由现有 50 ms 控制帧和服务端安全限幅维持。语音服务响应表示请求已提交，不代表 APP 能精确获得扬声器播放结束时间。

桥接协议新增：

```json
{"type":"voice_play","preset":"award_intro","sequence":42}
```

服务端在 `hello_ack` 中返回 `voice_presets`，APP 据此生成按钮。语音错误会返回 `voice_failed`，并显示在移动页面。

## AP 模式

AgiBot 官方 AimDK 文档说明 X2 可在官方 APP 中开启机器人 AP 热点。手机连接该热点后，APP 使用地址栏中的 WebSocket 地址连接桥接服务；本项目仍然是“手机 APP → PC2 Bridge → ROS 2/AimDK”，手机不直接连接 ROS。

本地代码不自动切换手机 Wi-Fi，也不假设 `192.168.88.88:8765` 一定就是 Bridge。现场按官方 APP 开启 AP 后，在本项目 APP 地址栏填写现场实际可访问的 Bridge WebSocket 地址即可。本次没有真机网络条件，AP 到 PC2 的可达性不在本次验证范围内。

官方参考：

- [语音控制](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/interactor/voice.html)
- [网络连接与 AP 模式](https://x2-aimdk.agibot.com/zh-cn/latest/quick_start/prerequisites.html#network-connection)
- [手机 AP 热点连接](https://x2-aimdk.agibot.com/zh-cn/latest/operation_guide/robot_connection.html#ap)

## 启动

Bridge 默认读取仓库中的 `config/voice_presets.json`，也可以指定另一份配置：

```bash
uv run x2-teleop-bridge --robot x2 --host 0.0.0.0 --port 8765 \
  --voice-config /path/to/voice_presets.json
```

现场首次使用前，应在机器人网络环境检查：

```bash
ros2 service list | rg 'PlayTts|PlayAudioFile'
```

没有对应服务时，TTS 或固定音频按钮会返回明确错误，不会影响移动控制服务本身启动。
