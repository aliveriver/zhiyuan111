#!/usr/bin/env bash
# 本脚本只管理本机进程，不执行 SSH；默认行为为后台重启桥接服务。
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ACTION=restart
FOREGROUND=0
ROBOT=auto
HOST=0.0.0.0
PORT=8765
TIMEOUT=0.4
SOURCE=mobile_app
CONFIG="$PROJECT_DIR/config/controller.json"
COBRIDGE_SETUP="${COBRIDGE_SETUP:-/agibot/software/cobridge/setup.bash}"
STATE_DIR="${X2_BRIDGE_STATE_DIR:-${TMPDIR:-/tmp}}"
PID_FILE="${X2_BRIDGE_PID_FILE:-$STATE_DIR/x2-ps5-teleop-${UID}.pid}"
LOG_FILE="${X2_BRIDGE_LOG_FILE:-$STATE_DIR/x2-ps5-teleop-${UID}.log}"

usage() {
    cat <<'EOF'
用法: ./scripts/start_mobile_bridge.sh [start|restart|stop|status] [选项]
默认不带命令执行 restart；PC2/run 自动使用 x2，其它环境使用 mock。
服务默认以 nohup 后台运行，SSH 或终端关闭不会停止服务。
选项: --robot mock|x2 --host ADDRESS --port PORT --timeout SECONDS
      --source NAME --config PATH --cobridge PATH
      --pid-file PATH --log-file PATH -h|--help
EOF
}
die() { echo "错误：$*" >&2; exit 1; }
is_pid() { [[ "$1" =~ ^[0-9]+$ ]]; }

matches_bridge() {
    local pid="$1" args cwd
    is_pid "$pid" || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    [[ "$args" == *x2_ps5_teleop.bridge.server* || "$args" == *"$SCRIPT_DIR"*start_mobile_bridge.sh*--foreground* ]] || return 1
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    [[ "$cwd" == "$PROJECT_DIR" ]]
}

find_pids() {
    local pid saved=""
    if [[ -f "$PID_FILE" ]]; then
        IFS= read -r saved < "$PID_FILE" || true
        if is_pid "$saved" && matches_bridge "$saved"; then printf '%s\n' "$saved"; else rm -f -- "$PID_FILE"; saved=""; fi
    fi
    while IFS= read -r pid; do
        [[ -n "$pid" ]] && matches_bridge "$pid" && [[ "$pid" != "$saved" ]] && printf '%s\n' "$pid"
    done < <(pgrep -f '[x]2_ps5_teleop\.bridge\.server' 2>/dev/null || true)
}

stop_bridge() {
    local pids=() pid running=0
    mapfile -t pids < <(find_pids)
    if ((${#pids[@]} == 0)); then rm -f -- "$PID_FILE"; echo "桥接服务未运行。"; return 0; fi
    echo "正在停止旧桥接进程: ${pids[*]}"
    for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
    for _ in {1..100}; do
        running=0
        for pid in "${pids[@]}"; do if kill -0 "$pid" 2>/dev/null; then running=1; break; fi; done
        ((running == 0)) && break
        sleep 0.1
    done
    if ((running != 0)); then for pid in "${pids[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done; fi
    rm -f -- "$PID_FILE"
    echo "旧桥接已停止。"
}

status_bridge() {
    local pids=() pid
    mapfile -t pids < <(find_pids)
    if ((${#pids[@]} == 0)); then echo "桥接服务未运行。"; return 3; fi
    for pid in "${pids[@]}"; do echo "桥接服务运行中: PID=$pid"; done
    echo "日志: $LOG_FILE"
}

while (($# > 0)); do
    case "$1" in
        start|restart|stop|status) ACTION="$1"; shift ;;
        --foreground) FOREGROUND=1; shift ;;
        --robot|--host|--port|--timeout|--source|--config|--cobridge|--pid-file|--log-file)
            (($# >= 2)) || die "$1 缺少参数"
            case "$1" in
                --robot) ROBOT="$2" ;; --host) HOST="$2" ;; --port) PORT="$2" ;;
                --timeout) TIMEOUT="$2" ;; --source) SOURCE="$2" ;; --config) CONFIG="$2" ;;
                --cobridge) COBRIDGE_SETUP="$2" ;; --pid-file) PID_FILE="$2" ;; --log-file) LOG_FILE="$2" ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1（使用 --help 查看帮助）" ;;
    esac
done

case "$ACTION" in
    stop) stop_bridge; exit 0 ;;
    status) status_bridge; exit $? ;;
esac
[[ -f "$CONFIG" ]] || die "配置文件不存在: $CONFIG"
LOCAL_ADDRESSES="$(hostname -I 2>/dev/null || true)"
[[ "$LOCAL_ADDRESSES" == *10.0.1.40* ]] && die "检测到 PC1 地址 10.0.1.40；请在 PC2 运行此脚本"
if [[ "$ROBOT" == auto ]]; then
    # PC2 may use a DHCP address from the phone hotspot. The wired
    # 10.0.1.41 address is only one deployment path and must not decide the
    # backend. The vendor run user identifies the robot-side deployment; the
    # X2 branch below validates the AimDK environment. Other users remain on
    # the safe Mock default. Do not require a particular interface address.
    if [[ "$(id -un)" == run ]]; then ROBOT=x2; else ROBOT=mock; fi
fi
case "$ROBOT" in mock|x2) ;; *) die "--robot 只能是 mock 或 x2" ;; esac

if [[ "$ROBOT" == x2 ]]; then
    [[ "$(id -un)" == run ]] || die "真机模式必须使用官方 run 用户，当前用户: $(id -un)"
    [[ -f "$COBRIDGE_SETUP" ]] || die "找不到 AimDK 环境: $COBRIDGE_SETUP"
    set +u
    # shellcheck disable=SC1090
    source "$COBRIDGE_SETUP"
    set -u
    ROS_PYTHON=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages
    PYTHONPATH=/agibot/software/common/local/lib/python3.10/dist-packages:$ROS_PYTHON:$PROJECT_DIR/src
    for path in /agibot/software/common /agibot/software/ec; do [[ -d "$path" ]] && AMENT_PREFIX_PATH="$path:${AMENT_PREFIX_PATH:-}"; done
    for path in /agibot/software/ec/lib /agibot/software/common/lib; do [[ -d "$path" ]] && LD_LIBRARY_PATH="$path:${LD_LIBRARY_PATH:-}"; done
    [[ -f /agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so ]] && LD_PRELOAD=/agibot/software/common/lib/libaimdk_msgs__rosidl_generator_py.so${LD_PRELOAD:+:$LD_PRELOAD}
    export PYTHONPATH AMENT_PREFIX_PATH LD_LIBRARY_PATH LD_PRELOAD
fi
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"
if [[ -n "${PYTHON_BIN:-}" ]]; then :; elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"; else PYTHON_BIN=python3; fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "找不到 Python: $PYTHON_BIN"

run_server() {
    "$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 websockets 或 numpy，请先执行 uv sync"
import websockets, numpy
print(f"websockets {websockets.__version__}")
print(f"numpy {numpy.__version__}")
PY
    if [[ "$ROBOT" == x2 ]]; then
        "$PYTHON_BIN" - <<'PY' || die "当前 Python 环境缺少 rclpy、aimdk_msgs 或其依赖"
import rclpy, aimdk_msgs
print("ROS 2 / aimdk_msgs ok")
PY
    fi
    cd "$PROJECT_DIR"
    echo "Python: $PYTHON_BIN"
    "$PYTHON_BIN" - <<'PY'
import sys, x2_ps5_teleop
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
    [[ "$ROBOT" == mock ]] && echo "安全提示: 当前为 Mock 模式，不会连接机器人。" || echo "安全提示: 当前为 X2 真机模式，请确认物理急停有人值守。"
    exec "$PYTHON_BIN" -m x2_ps5_teleop.bridge.server --robot "$ROBOT" --host "$HOST" --port "$PORT" --timeout "$TIMEOUT" --source "$SOURCE" --config "$CONFIG"
}

if ((FOREGROUND == 1)); then
    mkdir -p -- "$(dirname -- "$PID_FILE")"
    printf '%s\n' "$$" > "$PID_FILE"
    run_server
fi

stop_bridge
mkdir -p -- "$(dirname -- "$PID_FILE")" "$(dirname -- "$LOG_FILE")"
echo "正在后台启动桥接服务（后端=$ROBOT，端口=$PORT）..."
nohup bash "$SCRIPT_DIR/$(basename -- "$0")" --foreground --robot "$ROBOT" --host "$HOST" --port "$PORT" --timeout "$TIMEOUT" --source "$SOURCE" --config "$CONFIG" --cobridge "$COBRIDGE_SETUP" --pid-file "$PID_FILE" --log-file "$LOG_FILE" >> "$LOG_FILE" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE"
command -v disown >/dev/null 2>&1 && disown "$pid" 2>/dev/null || true
sleep 0.3
if matches_bridge "$pid"; then
    echo "桥接服务已启动: PID=$pid"
    echo "日志: $LOG_FILE"
    exit 0
fi
echo "桥接服务启动失败，最近日志如下：" >&2
tail -40 "$LOG_FILE" >&2 || true
rm -f -- "$PID_FILE"
exit 1
