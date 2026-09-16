# X2 Ultra v0.9.7：PC2 部署手册

适用主机：PC2 `10.0.1.41`，用户 `run`，项目 `/home/run/zhiyuan111`。本方案不升级固件、不修改 PC1 的 MC/HAL/状态机配置，也不部署独立 OmniHand 驱动。

当前功能及限制见 [App 手册](APP_USER_GUIDE.md)，接口证据见 [调查报告](X2_V0_9_7_CONTROL_INVESTIGATION.md)。部署本版本不会解锁真机手部执行和轨迹播放；能使用的是现有人工移动、状态采样、轨迹与位置预设管理。启动 X2 后端会注册 MC 输入源，不是纯只读诊断。

## 1. 停止旧桥接并备份

在机器人静止、现场人员值守物理急停的条件下，先退出 App TELEOP，再在旧桥接终端按 Ctrl+C。不要停止厂商 MC/HAL 服务。

PC2 上检查是否仍有旧桥接占用端口：

```bash
ss -ltnp 'sport = :8765'
pgrep -af x2_ps5_teleop.bridge.server
```

确认进程归属后在原启动终端退出，不使用广泛的 `pkill python`。不要同时启动两个真机桥接。

在 PC2 的 `run` 用户 shell 备份，终端保留本次备份路径：

```bash
cd /home/run/zhiyuan111
backup_dir="/home/run/x2-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
tar --exclude='.venv' --exclude='.uv-cache' --exclude='mobile/node_modules' \
    --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' \
    -czf "$backup_dir/project.tar.gz" .
cp -a /home/run/.x2_ps5_teleop "$backup_dir/data"
sha256sum /home/run/.x2_ps5_teleop/trajectories.json | tee "$backup_dir/trajectories.sha256"
printf '本次备份：%s\n' "$backup_dir"
```

已有轨迹不删除、不清空、不用代码包中的样例覆盖。代码更新不需要恢复或改写数据文件。

## 2. 上传代码

在开发电脑的仓库根目录执行：

```bash
tar --exclude='mobile/node_modules' --exclude='mobile/.expo' --exclude='__pycache__' \
    -czf /tmp/x2-award-code.tar.gz \
    src tests mobile docs pyproject.toml uv.lock README.md
scp /tmp/x2-award-code.tar.gz run@10.0.1.41:/tmp/x2-award-code.tar.gz
```

PC2 解包：

```bash
cd /home/run/zhiyuan111
tar -xzf /tmp/x2-award-code.tar.gz
sha256sum /home/run/.x2_ps5_teleop/trajectories.json
```

该包不包含 `.venv`、厂商软件、轨迹目录、现有 `config/controller.json` 或启动脚本。解包不使用删除参数；核对轨迹哈希与备份一致。

## 3. Python 环境和代码验证

沿用 PC2 已经工作正常的 `.venv`。ROS Humble Python 绑定需要与系统 Python 3.10 兼容，不要复制开发电脑的 `.venv`，也不要升级系统 Python。

```bash
cd /home/run/zhiyuan111
.venv/bin/python --version
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q
.venv/bin/python -c 'import numpy, websockets; print(numpy.__version__, websockets.__version__)'
```

本次代码没有新增运行时 Python 依赖。如果 `.venv` 不存在，使用 PC2 的 Python 3.10 创建环境，再按 `pyproject.toml` 安装；不要覆盖厂商 Python 包。例如已有 `uv` 时：

```bash
uv sync --python /usr/bin/python3.10
```

若安装失败，应先解决依赖与 ROS Python 兼容性，不要更改厂商库或尝试关闭 MC。

## 4. 先运行 Mock

在 PC2 或开发电脑上都可执行，Mock 不初始化 ROS、不连接机器人：

```bash
cd /home/run/zhiyuan111
PYTHONPATH="$PWD/src" .venv/bin/python -m x2_ps5_teleop.bridge.server \
    --robot mock --host 0.0.0.0 --port 8765 \
    --data-dir /tmp/x2-award-mock-data \
    --config /home/run/zhiyuan111/config/controller.json
```

Mock 命令明确使用独立的 `/tmp/x2-award-mock-data`，不会将模拟录制写入真机轨迹库。正式 X2 启动保持默认数据目录。不要省略 Mock 的 `--data-dir` 后在生产目录制作示例轨迹。

使用 App 确认页面明确标为模拟；验证位置预设保存、交接确认、录制、重复播放、暂停/继续/停止。完成后 Ctrl+C 退出。

## 5. 启动 X2 桥接

确认 `hostname -I` 包含 `10.0.1.41`，`id -un` 为 `run`，旧桥接端口已释放。以下命令直接调用模块，不依赖工作区已有启动脚本的换行格式。

```bash
cd /home/run/zhiyuan111
source /agibot/software/cobridge/setup.bash
export PYTHONPATH="$PWD/src:/agibot/software/common/local/lib/python3.10/dist-packages:/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages"
export AMENT_PREFIX_PATH="/agibot/software/common:/agibot/software/ec:${AMENT_PREFIX_PATH:-}"
export LD_LIBRARY_PATH="/agibot/software/common/lib:/agibot/software/ec/lib:${LD_LIBRARY_PATH:-}"
export LD_PRELOAD="/agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so${LD_PRELOAD:+:$LD_PRELOAD}"
.venv/bin/python -c 'import rclpy, aimdk_msgs; print(aimdk_msgs.__file__)'
.venv/bin/python -m x2_ps5_teleop.bridge.server \
    --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app \
    --config /home/run/zhiyuan111/config/controller.json
```

`common` 的完整 `aimdk_msgs` 必须优先，不能被 `ec` 的同名精简包遮蔽。保留前台终端观察日志。首次部署不配置开机自动启动或无人值守自动重启。

启动成功应看到 WebSocket 监听日志及正确的源码路径。App 连接后应显示：当前固件独立手部控制和上肢动画停止链路未确认，真机执行未开放。这是预期行为。

## 6. 启动手机 App

推荐在开发电脑的 `mobile` 目录运行 Expo，PC2 只运行 Python 桥接：

```bash
cd mobile
npm ci
npm run typecheck
npm start
```

用项目所需 Expo/React Native 版本兼容的客户端打开 Metro 给出的入口。桥接地址填 `ws://10.0.1.41:8765`。手机既需要访问 Expo 开发电脑，也需要访问 PC2；不能将 Metro 地址当作机器人 WebSocket 地址。

若使用自行构建的安装包，更新包后也要核对能力说明和状态录制按钮文字，避免旧 App 默认发 `teach_mode:true`。

## 7. 部署验收

1. 核对部署前后的轨迹哈希，检查旧轨迹仍在列表。
2. 连接 X2，确认后端说明不是 Mock。
3. 先读取当前双手位置；没有反馈时不要点击任何状态迁移或电机上下电操作。
4. 在现场受控条件下，20 Hz 录制短时静止状态，停止保存，检查臂 14 / 左手 10 / 右手 10。此步骤只验证采集，不证明动作回放。
5. 确认真机手部发送和轨迹播放按钮不可用，旧版手部消息也会被服务端拒绝。
6. 核对退出 TELEOP、断连及心跳超时后的日志和零速请求。软件零速不等于物理急停，不能据此宣称机械臂安全停止已验收。

完整颁奖验收仍需厂商确认 v0.9.7 的手部仲裁和可停止的上肢动画协议，之后再进行单关节空载 20 Hz、3–5 秒、停止验证和 0.25x 全轨迹测试。当前不要执行 `aima em stop-app mc`、`MigrateSystemState`、`SetDcuMotorPowerState` 或直接向 HAL 发布来绕过限制。

## 8. 数据、日志与回滚

- 轨迹：`/home/run/.x2_ps5_teleop/trajectories.json`
- 手部预设：`/home/run/.x2_ps5_teleop/hand_poses.json`
- 代码：`/home/run/zhiyuan111`
- 日志：前台 stdout/stderr；按需由终端保存，日志不包含登录口令。

回滚时先退出 App、在桥接终端 Ctrl+C，再将备份代码解压到单独目录检查。确认后恢复代码；保留当前数据目录，不把旧备份直接覆盖新录制。旧代码可能没有本次增加的 HAL 手命令拦截，回滚后不要用旧手部按钮规避保护。

故障定位：

| 现象 | 检查 |
| --- | --- |
| `aimdk_msgs` 导入失败或缺少消息类型 | Python 版本、common 包优先级、LD_LIBRARY_PATH/LD_PRELOAD |
| 8765 被占用 | `ss -ltnp`，确认并退出旧桥接，不能盲目杀系统进程 |
| App 未显示能力说明 | 核对运行源码路径，确认 App/桥接均为本次版本 |
| 录制缺少状态 | 只读检查 arm/state 与 hand/state，不发布测试命令 |
| 预设文件损坏导致启动失败 | 备份原文件、人工检查 JSON；不删除文件来绕过错误 |
| `Develop_MC` 报错 | 旧 App 正在请求卸力示教，更新 App 并使用状态录制；不要迁移系统状态 |
