# X2 Mobile Teleop

这是 AgiBot X2 手机遥操作 Expo App。App 只连接 PC2 的 WebSocket 桥接服务，不直接连接
ROS 2 或 PC1。

## 开发启动

在仓库根目录先启动 Mock 桥接：

```powershell
uv sync
uv run x2-teleop-bridge --robot mock --host 0.0.0.0 --port 8765
```

另开终端启动 App：

```powershell
cd mobile
npm install
npm run typecheck
npx expo start -c
```

如果 Metro 报 `Unable to resolve "expo-network"`，请确认命令是在 `mobile` 目录执行，
然后删除旧依赖并按锁文件重装：

```powershell
cd mobile
Remove-Item -Recurse -Force node_modules
npm ci
npx expo start -c
```

生成可直接安装到 Android 手机上的 APK：

```powershell
npx eas-cli build --platform android --profile preview
```

构建完成后使用 EAS 输出的下载链接或二维码安装 APK。首次构建需要登录 Expo 账号：

```powershell
npx eas-cli login
```

用 Expo Go 或 EAS APK 打开项目。手机和电脑连接同一局域网后，地址栏留空并点击“自动发现并连接”，App 会读取手机 IPv4 并扫描同一 `/24` 子网的 `8765` 端口，通过 WebSocket 握手确认 PC2 桥接服务。也可以手动输入 `ws://<电脑局域网IP>:8765`。Mock 模式会在服务端终端打印运动、模式和预设动作，不会控制真机。

EAS 独立包不依赖 Expo Go 的 Metro 连接；PC2 必须以 `--host 0.0.0.0 --port 8765` 启动桥接，手机与 PC2 不能使用访客 Wi-Fi/AP 隔离。Android 已允许局域网明文 WebSocket，iOS 首次扫描时需要允许“本地网络”权限。

## 真机启动

真机只能在 PC2（通常为 `10.0.1.41`）运行桥接服务，不能在 PC1（`10.0.1.40`）运行：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
x2-teleop-bridge --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app
```

手机填写 `ws://10.0.1.41:8765` 或现场实际的 PC2 地址。启动时 App 为 `IDLE`，点击
“进入 TELEOP”后才接受摇杆。

## 操作和安全

- 左摇杆控制前进、后退和横移，右摇杆控制左右旋转；摇杆支持连续拖动，松手归零。
- 松开摇杆、切后台、断网或超过 0.4 秒无控制帧时，服务端会发零速度。
- 同时只允许一个手机控制；同一手机新连接会替换旧连接。
- 移动控制页提供“握紧、张开、比个耶、点个赞”四个手部预设；参数页可按左右手逐项填写 10 个关节的位置、速度和力度并发送单帧。手部命令会先停车，完成后回到 `IDLE`。
- 急停会锁存；确认安全后必须清除急停并重新进入 TELEOP。
- 真机测试前必须清空周围空间并安排人员值守物理急停。

移动控制页的“固定语音”按钮由 PC2 Bridge 的 `config/voice_presets.json` 动态提供。
配置中有几条预设就显示几个按钮；当前配置显示“十年赛事回顾”和“总结十年赛事”。每个
`mode=file` 音频文件需按官方要求放在 PC3。语音请求不停车，发送期间可以继续使用移动控制页。

完整网络、部署和故障排查说明见 [docs/MOBILE_APP_PLAN.md](../docs/MOBILE_APP_PLAN.md)，
语音和 AP 记录见 [docs/VOICE_AP_MODE.md](../docs/VOICE_AP_MODE.md)。
