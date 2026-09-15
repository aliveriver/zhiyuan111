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
npm install
npm run typecheck
npm start
```

用 Expo Go 或 Development Build 打开项目。手机和电脑连接同一局域网，在 App 的地址栏
输入 `ws://<电脑局域网IP>:8765`，点击“连接”。Mock 模式会在服务端终端打印运动、模式
和预设动作，不会控制真机。

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

- 左摇杆控制前后/横移，右摇杆控制旋转。
- 松开摇杆、切后台、断网或超过 0.4 秒无控制帧时，服务端会发零速度。
- 同时只允许一个手机控制；同一手机新连接会替换旧连接。
- 手部预设动作会先停车，完成后回到 `IDLE`。
- 急停会锁存；确认安全后必须清除急停并重新进入 TELEOP。
- 真机测试前必须清空周围空间并安排人员值守物理急停。

完整网络、部署和故障排查说明见 [docs/MOBILE_APP_PLAN.md](../docs/MOBILE_APP_PLAN.md)。
