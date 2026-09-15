#!/usr/bin/env bash
# 启动 X2 手机 WebSocket 桥接服务。
# 默认启动 Mock；只有显式传入 --robot x2 才会连接真机。

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

ROBOT="mock"
HOST="0.0.0.0"
PORT="8765"
TIMEOUT="0.4"
SOURCE="mobile_app"
CONFIG="$PROJECT_DIR/config/controller.json"
COBRIDGE_SETUP="${COBRIDGE_SETUP:-/agibot/software/cobridge/setup.bash}"

usage() {
    cat <<'EOF'
用法:
  ./scripts/start_mobile_bridge.sh [选项]

选项:
  --robot mock|x2       后端类型，默认 mock；真机必须显式使用 x2
  --host ADDRESS        WebSocket 监听地址，默认 0.0.0.0
  --port PORT           WebSocket 端口，默认 8765
  --timeout SECONDS     控制帧超时，默认 0.4
  --source NAME         AimDK 输入源名称，默认 mobile_app
  --config PATH         控制器配置，默认 config/controller.json
  --cobridge PATH       cobridge/setup.bash 路径
  -h, --help            显示帮助

示例:
  ./scripts/start_mobile_bridge.sh
  ./scripts/start_mobile_bridge.sh --robot mock
  ./scripts/start_mobile_bridge.sh --robot x2 --port 8765
EOF
}

die() {
    echo "错误：$*" >&2
    exit 1
}

while (($# > 0)); do
    case "$1" in
        --robot|--host|--port|--timeout|--source|--config|--cobridge)
            (($# >= 2)) || die "$1 缺少参数"
            case "$1" in
                --robot) ROBOT="$2" ;;
                --host) HOST="$2" ;;
                --port) PORT="$2" ;;
                --timeout) TIMEOUT="$2" ;;
                --source) SOURCE="$2" ;;
                --config) CONFIG="$2" ;;
                --cobridge) COBRIDGE_SETUP="$2" ;;
            esac
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "未知参数: $1（使用 --help 查看帮助）"
            ;;
    esac
done

case "$ROBOT" in
    mock|x2) ;;
    *) die "--robot 只能是 mock 或 x2" ;;
esac

[[ -f "$CONFIG" ]] || die "配置文件不存在: $CONFIG"

# 已知 PC1 是运控计算机，禁止在上面运行二开桥接程序。
LOCAL_ADDRESSES="$(hostname -I 2>/dev/null || true)"
if [[ $LOCAL_ADDRESSES == *10.0.1.40* ]]; then
    die "检测到 PC1 地址 10.0.1.40；请在 PC2（通常为 10.0.1.41）运行此脚本"
fi

if [[ "$ROBOT" == "x2" ]]; then
    [[ "$(id -un)" == "run" ]] || die "真机模式必须使用官方 run 用户，当前用户: $(id -un)"
    [[ -f "$COBRIDGE_SETUP" ]] || die "找不到 AimDK 环境: $COBRIDGE_SETUP"
    # shellcheck disable=SC1090
    source "$COBRIDGE_SETUP"
fi

export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 Python: $PYTHON_BIN"

"$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 websockets，请先安装 websockets>=12"
import websockets
print(f"websockets {websockets.__version__}")
PY

if [[ "$ROBOT" == "x2" ]]; then
    "$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 rclpy 或 aimdk_msgs，请确认已加载 cobridge 环境"
import rclpy
import aimdk_msgs
print("ROS 2 / aimdk_msgs ok")
PY
fi

cd "$PROJECT_DIR"
echo "项目目录: $PROJECT_DIR"
echo "控制后端: $ROBOT"
echo "监听地址: ws://$HOST:$PORT"
echo "输入源: $SOURCE"
if [[ "$ROBOT" == "mock" ]]; then
    echo "安全提示: 当前为 Mock 模式，不会连接机器人。"
else
    echo "安全提示: 当前为 X2 真机模式，请确认物理急停有人值守。"
fi

exec "$PYTHON_BIN" -m x2_ps5_teleop.bridge.server \
    --robot "$ROBOT" \
    --host "$HOST" \
    --port "$PORT" \
    --timeout "$TIMEOUT" \
    --source "$SOURCE" \
    --config "$CONFIG"
