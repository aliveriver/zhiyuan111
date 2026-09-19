# 使用说明

1. 清空机器人周围空间，确认物理急停和阻尼回退可用。
2. 在蓝牙设置中配对 DualSense，按 PS 键唤醒；先运行 `uv run x2-ps5-input-test` 确认轴和按钮编号。
3. 启动 `uv run python demo/teleop_demo.py --robot mock` 完成 Mock 验证，再在开发计算单元上使用 `--robot x2`。
4. 按 `OPTIONS` 进入 `TELEOP`，按 `△` 进入位控，按 `□` 进入官方 `STAND_DEFAULT` 稳定站立模式。
5. 左摇杆控制前后/横移，右摇杆控制转向。
6. `RT/LT/R1/L1` 当前触发的是已验证的上肢挥手/举手动作，不是灵巧手手指姿态；在手部
   关节顺序、单位和限位确认前，不要将这些事件配置为张手、闭合、抓取或释放。
7. 触发任意上肢动作时程序会立即停止底盘并退回 `IDLE`；动作完成并确认状态正常后，
   再按一次 `OPTIONS` 恢复移动。即使同时按下多个动作键，每次也只执行一个预设。
8. 按 `○` 进入阻尼，按 `×` 进入零力矩；按 `PS` 触发软件急停，需人工清除后才能重新进入 TELEOP。

首版输入是事件触发，手指不会实时跟随手柄。动作 ID、模式服务和控制权由机器人侧适配层配置。

## 手机 Expo App 使用

手机 App 不直接连接 ROS 2，只连接 PC2 上的 WebSocket 桥接服务。首次使用建议先用
Mock 后端完成联调，再切换到真机后端。

### 1. 启动 Mock 桥接

在项目根目录执行：

```powershell
uv sync
uv run pytest
uv run x2-teleop-bridge --robot mock --host 0.0.0.0 --port 8765
```

桥接服务启动后保持 `IDLE`，不要关闭这个终端。

### 2. 启动 Expo App

另开终端执行：

```powershell
cd mobile
npm install
npm run typecheck
npm start
```

用 Expo Go、Development Build 或 EAS APK 打开 App。手机和电脑必须连接同一个允许设备互访的
Wi-Fi，地址栏留空并点击“自动发现并连接”；App 会扫描手机所在 `/24` 子网中的桥接服务。也可在地址栏填写 `ws://电脑局域网IP:8765`，点击“连接”。如果电脑地址为
`10.0.1.41`，则填写 `ws://10.0.1.41:8765`。

### 3. Mock 操作顺序

1. 确认 App 显示“已连接”，服务状态为 `IDLE · 未解锁`。
2. 点击“进入 TELEOP”，确认状态变为 `TELEOP · 已解锁`。
3. 拖动左摇杆测试前后/横移，拖动右摇杆测试旋转；Mock 终端应持续打印 `MOVE`。
4. 点击运动模式或手部预设按钮，确认终端收到对应事件。
5. 点击“急停”或关闭 App，确认服务端打印零速度。

### 4. 真机操作顺序

真机只能在 PC2（通常为 `10.0.1.41`）启动，不能在 PC1（`10.0.1.40`）启动。使用
官方 `run` 用户加载 AimDK 环境：

```bash
cd /agibot/data/home/agi/x2_ps5_teleop
source /agibot/software/cobridge/setup.bash
export PYTHONPATH=$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/agibot/software/ec/local/lib/python3.10/dist-packages:$PYTHONPATH
export AMENT_PREFIX_PATH=/agibot/software/common:/agibot/software/ec:$AMENT_PREFIX_PATH
export LD_LIBRARY_PATH=/agibot/software/common/lib:/agibot/software/ec/lib:$LD_LIBRARY_PATH
x2-teleop-bridge --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app
```

开始前必须清空机器人周围空间、确认物理急停和阻尼回退可用，并先在 App 中观察
`IDLE` 状态。只有确认机器人状态正常后才点击“进入 TELEOP”，首轮只做低速、短时、
空旷环境测试。

### 5. 并发与自动停车

- 同时只有一个手机连接拥有控制租约；其他手机会收到 `busy`。
- 同一手机重新连接时，新连接会使旧连接失效。
- 速度帧必须使用递增 `sequence`，重复或乱序帧会被拒绝。
- 速度帧由服务端按 `0.12 m/s`、`0.08 m/s`、`0.15 rad/s` 再次限幅。
- 0.4 秒没有收到速度帧或心跳，服务端发送零速度并进入 `TIMEOUT`。
- 切后台、锁屏、关闭 App、WebSocket 断开都会停止发送运动控制；服务端断连处理也会停车。
- 预设手部动作执行前会停车，执行后回到 `IDLE`，必须重新解锁。
