# 项目说明

目标是先跑通 X2 的最小遥操闭环：PS5 控制移动，按钮切换模式，RT/LT/R1/L1 触发四个动作事件。
当前已验证的事件调用的是上肢挥手/举手预设；真正的灵巧手指令需要确认 `HandCommandArray`
关节顺序、单位和限位后再接入。

控制核心负责轴值死区、速度缩放、按钮上升沿去抖、事件分发和手柄超时回退。机器人消息和 AimDK 服务由 ROS 2 适配层负责。

当前已知的两种输入路径：

```text
PC2 配对 DualSense -> pygame -> 安全状态机 -> AimDK 适配器
手机 Expo App -> PC2 WebSocket 桥接 -> 安全状态机 -> AimDK 适配器
```

官方“DualSense 连接机器人”路径由 PC1 的 `soc0_rc` 内部消费，没有公开 `/joy` 输入 Topic，
不能直接供 PC2 的 pygame 读取。手机方案见 [MOBILE_APP_PLAN.md](MOBILE_APP_PLAN.md)。

首版采用阶段式控制权：启动为 IDLE；OPTIONS 切换 TELEOP；手部动作前发送零速度，再调用官方预设动作服务。断连、超时和软件急停均锁定零速度，物理急停始终保留。
