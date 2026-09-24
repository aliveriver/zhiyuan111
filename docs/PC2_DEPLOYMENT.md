# X2 Ultra v0.9.7：PC2 部署手册

适用主机：PC2 `10.0.1.41`，用户 `run`，项目 `/home/run/zhiyuan111`。本方案不升级固件、不修改 PC1 的 MC/HAL/状态机配置，也不部署独立 OmniHand 驱动。

当前功能及限制见 [App 手册](APP_USER_GUIDE.md)，接口证据见 [调查报告](X2_V0_9_7_CONTROL_INVESTIGATION.md)。部署本版本恢复真机手部五参数和位置预设执行；支持现有人工移动、状态采样、轨迹管理和独立 MC 回放。未提供 commissioning 文件时以保守限制进入醒目的有人值守测试模式；启动 X2 后端会注册 MC 输入源，不是纯只读诊断。

## 1. 备份及运行进程边界

当前现场未准备好时，只备份、同步代码和做离线验证，**不自动停止或重启现有桥接，不启动 X2 后端，不发送运动命令**。文件同步不会使运行中的旧 Python 进程自动升级。现场准备好后，在机器人静止、物理急停有人值守时先退出 App TELEOP，再由操作员在旧终端 Ctrl+C。不要停止厂商 MC/HAL 服务。

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
    --robot mock --host 0.0.0.0 --port 18765 \
    --data-dir /tmp/x2-award-mock-data \
    --config /home/run/zhiyuan111/config/controller.json
```

Mock 命令明确使用独立的 `/tmp/x2-award-mock-data`，不会将模拟录制写入真机轨迹库。正式 X2 启动保持默认数据目录。不要省略 Mock 的 `--data-dir` 后在生产目录制作示例轨迹。

使用 App 确认页面明确标为模拟；验证位置预设保存、交接确认、录制、重复播放、暂停/继续/停止。完成后 Ctrl+C 退出。

## 5. 启动 X2 桥接

确认当前主机是 PC2、`id -un` 为 `run`，并已接入机器人同一网络（有线地址可能是
`10.0.1.41`，手机热点下通常是 DHCP 动态地址）。启动脚本会根据 `run` 用户和
AimDK 环境选择 X2 后端，不再要求固定 IP；它也会停止本项目旧桥接并在后台托管新进程。

```bash
cd /home/run/zhiyuan111
./scripts/start_mobile_bridge.sh restart --robot x2 --host 0.0.0.0 --port 8765 --source mobile_app
./scripts/start_mobile_bridge.sh status
```

`common` 的完整 `aimdk_msgs` 必须优先，不能被 `ec` 的同名精简包遮蔽。日志默认在 `/tmp/x2-ps5-teleop-$UID.log`；停止或重启使用脚本的 `stop`/`restart`，不要直接 `pkill python`。

启动成功应看到 WebSocket 监听日志及正确的源码路径。App 连接后应显示“未校验 commissioning 文件的有人值守测试模式”，轨迹回放可用但仍要求 TELEOP、稳定站立、MC 空闲和匹配的起始姿态。

## 6. 启动手机 App

推荐在开发电脑的 `mobile` 目录运行 Expo，PC2 只运行 Python 桥接：

```bash
cd mobile
npm ci
npm run typecheck
npm start
```

用项目所需 Expo/React Native 版本兼容的客户端打开 Metro 给出的入口。桥接地址填 `ws://10.0.1.41:8765`。手机既需要访问 Expo 开发电脑，也需要访问 PC2；不能将 Metro 地址当作机器人 WebSocket 地址。

本轮滑条使用 Expo SDK 匹配的 `@react-native-community/slider`，需同步 `package.json` 和 `package-lock.json` 后在 App 开发电脑运行 `npm ci`。自建开发客户端/安装包需要重新构建以包含原生模块，单独热更新 JS 不一定足够；使用匹配的 Expo Go 时沿用其内置模块。PC2 若仅运行 Python 桥接则无需安装 npm 依赖。

若使用自行构建的安装包，更新包后也要核对能力说明和状态录制按钮文字，避免旧 App 默认发 `teach_mode:true`。MC 动画测试另见 [验收说明](MC_ANIMATION_COMMISSIONING.md)。

## 7. 部署验收

1. 核对部署前后的轨迹哈希，检查旧轨迹仍在列表。
2. 连接 X2，确认后端说明不是 Mock。
3. 先读取当前双手位置；没有反馈时不要点击任何状态迁移或电机上下电操作。
4. 在现场受控条件下，20 Hz 录制短时静止状态，停止保存，检查臂 14 / 左手 10 / 右手 10。此步骤只验证采集，不证明动作回放。
5. 确认手部参数按钮在 TELEOP 且无播放活动时可用。由现场人员使用已验证参数进行所选手的空载小幅测试，检查另一只手不变；读取反馈，保存位置预设，再验证原始符号回放。轨迹播放只在空载、物理急停有人值守时测试。
6. 核对退出 TELEOP、断连及心跳超时后的日志和零速请求。软件零速不等于物理急停，不能据此宣称机械臂安全停止已验收。

完整颁奖验收按 [MC 验收说明](MC_ANIMATION_COMMISSIONING.md) 分阶段进行：单关节空载 ≤0.02 rad / 3 秒、中途停止、暂停继续与断连，再做 0.25x 全轨迹测试。当前不要执行 `aima em stop-app mc`、`MigrateSystemState`、`SetDcuMotorPowerState` 或直接向 HAL 手臂话题发布来绕过限制。

## 8. 数据、日志与回滚

- 轨迹：`/home/run/.x2_ps5_teleop/trajectories.json`
- 手部预设：`/home/run/.x2_ps5_teleop/hand_poses.json`
- 代码：`/home/run/zhiyuan111`
- 日志：前台 stdout/stderr；按需由终端保存，日志不包含登录口令。

回滚时先退出 App、在桥接终端 Ctrl+C，再将备份代码解压到单独目录检查。确认后恢复代码；保留当前数据目录，不把旧备份直接覆盖新录制。回滚后确认 App 与桥接协议兼容；本版已恢复旧手部参数通道，不需要回滚来使用它。

故障定位：

| 现象 | 检查 |
| --- | --- |
| `aimdk_msgs` 导入失败或缺少消息类型 | Python 版本、common 包优先级、LD_LIBRARY_PATH/LD_PRELOAD |
| 8765 被占用 | `ss -ltnp`，确认并退出旧桥接，不能盲目杀系统进程 |
| App 未显示能力说明 | 核对运行源码路径，确认 App/桥接均为本次版本 |
| 录制缺少状态 | 只读检查 arm/state 与 hand/state，不发布测试命令 |
| 预设文件损坏导致启动失败 | 备份原文件、人工检查 JSON；不删除文件来绕过错误 |
| `Develop_MC` 报错 | 旧 App 正在请求卸力示教，更新 App 并使用状态录制；不要迁移系统状态 |


## 9. 可选的 commissioning 文件

参数 `--mc-commissioning-profile <JSON路径>` 用于加载已完成现场验收的报告。模板及字段见 [验收说明](MC_ANIMATION_COMMISSIONING.md)，不要将模板 false 批量改成 true。省略该参数不再关闭回放，而是进入保守限速、无远端文件哈希校验的有人值守测试模式，App 会持续显示警告。

准备好的报告放在 `/home/run/.x2_ps5_teleop/mc-commissioning.json`，报告正文可与其同目录。PC2→soc0 需要已核对的主机密钥及可用 SSH 密钥认证，BatchMode 上传不交互询问密码。每次开始/继续会重新核对 soc0 库与配置哈希、Business / STAND_DEFAULT / RUNNING / IDLE、反馈新鲜度与起点；不匹配即拒绝。

在第 5 节环境已设置且旧桥接已由现场操作员退出之后，给原 X2 启动命令增加：

```text
--mc-commissioning-profile /home/run/.x2_ps5_teleop/mc-commissioning.json
```

提供配置时 App 会标记为已 commissioning，并增加远端库和配置哈希核对。不提供参数时仍保留站立、MC 空闲、起点、限速、保持停止反馈、300 ms 稳定性和锁文件保护。遇到 stop_failed 时持续互锁；停止未确认文件不能手工删除绕过。
