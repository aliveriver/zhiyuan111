#!/usr/bin/env bash
# 启动 X2 手机 WebSocket 桥接服务。
# PC2/run 自动连接真机，其它环境默认使用 Mock；可用 --robot 显式覆盖。

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# On the robot PC2, a bare invocation should start the real backend. On any
# other host keep the safer Mock default; --robot always overrides detection.
ROBOT="auto"
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
  --robot mock|x2       后端类型，默认按主机自动判断（PC2/run=x2，其它=mock）
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

[[ -f "$CONFIG" ]] || die "配置文件不存在: $CONFIG"

# 已知 PC1 是运控计算机，禁止在上面运行二开桥接程序。
LOCAL_ADDRESSES="$(hostname -I 2>/dev/null || true)"
if [[ $LOCAL_ADDRESSES == *10.0.1.40* ]]; then
    die "检测到 PC1 地址 10.0.1.40；请在 PC2（通常为 10.0.1.41）运行此脚本"
fi

if [[ "$ROBOT" == "auto" ]]; then
    if [[ $LOCAL_ADDRESSES == *10.0.1.41* && "$(id -un)" == "run" ]]; then
        ROBOT="x2"
    else
        ROBOT="mock"
    fi
fi
case "$ROBOT" in
    mock|x2) ;;
    *) die "--robot 只能是 mock 或 x2" ;;
esac

if [[ "$ROBOT" == "x2" ]]; then
    [[ "$(id -un)" == "run" ]] || die "真机模式必须使用官方 run 用户，当前用户: $(id -un)"
    [[ -f "$COBRIDGE_SETUP" ]] || die "找不到 AimDK 环境: $COBRIDGE_SETUP"
    # shellcheck disable=SC1090
    # 官方 setup.bash 会读取未定义的 COLCON_TRACE；临时关闭 nounset，
    # 避免污染或修改官方环境脚本。
    set +u
    source "$COBRIDGE_SETUP"
    set -u
    # The factory setup script does not consistently export the Python and
    # AMENT paths needed by aimdk_msgs on every image, so add the known local
    # workspaces when they exist.
    # Use the complete common AimDK message package. The ec workspace carries
    # a reduced package with the same import name and must not shadow it.
    ROS_PYTHON="/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages"
    PYTHONPATH="/agibot/software/common/local/lib/python3.10/dist-packages:$ROS_PYTHON:$PROJECT_DIR/src"
    for path in /agibot/software/common /agibot/software/ec; do
        [[ -d "$path" ]] && AMENT_PREFIX_PATH="$path:${AMENT_PREFIX_PATH:-}"
    done
    for path in /agibot/software/ec/lib /agibot/software/common/lib; do
        [[ -d "$path" ]] && LD_LIBRARY_PATH="$path:${LD_LIBRARY_PATH:-}"
    done
    if [[ -f /agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so ]]; then
        LD_PRELOAD="/agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so${LD_PRELOAD:+:$LD_PRELOAD}"
    fi
    export PYTHONPATH AMENT_PREFIX_PATH LD_LIBRARY_PATH LD_PRELOAD
fi

export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
if [[ -n "${PYTHON_BIN:-}" ]]; then
    :
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 Python: $PYTHON_BIN"

"$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 websockets 或 numpy，请先在项目环境执行 uv sync"
import websockets
import numpy
print(f"websockets {websockets.__version__}")
print(f"numpy {numpy.__version__}")
PY

if [[ "$ROBOT" == "x2" ]]; then
    "$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 rclpy、aimdk_msgs 或其依赖，请确认已加载 cobridge 环境并执行 uv sync"
import rclpy
import aimdk_msgs
print("ROS 2 / aimdk_msgs ok")
PY
fi

cd "$PROJECT_DIR"
echo "Python: $PYTHON_BIN"
"$PYTHON_BIN" - <<'PY'
import sys
import x2_ps5_teleop
import x2_ps5_teleop.bridge.server as bridge_server
print(f"桥接源码: {bridge_server.__file__}")
print(f"协议版本: {bridge_server.PROTOCOL_VERSION}")
print(f"Python 模块路径: {x2_ps5_teleop.__file__}")
print(f"sys.path[0:3]: {sys.path[0:3]}")
PY
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
