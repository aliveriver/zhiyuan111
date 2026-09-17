import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  AppState,
  AppStateStatus,
  PanResponder,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import Slider from '@react-native-community/slider';
import { isPlaybackBusy, playbackAfterDisconnect, PlaybackState } from './playbackState';

// Official HAL active-axis order, also present in v0.9.7 animation_player.yaml.
const HAND_JOINTS = [
  ['thumb_roll_joint', '拇指 · 根部旋转'],
  ['thumb_abad_joint', '拇指 · 侧摆（外展／内收）'],
  ['thumb_mcp_joint', '拇指 · 掌指关节屈伸'],
  ['index_abad_joint', '食指 · 侧摆（外展／内收）'],
  ['index_pip_joint', '食指 · 屈伸'],
  ['middle_pip_joint', '中指 · 屈伸'],
  ['ring_abad_joint', '无名指 · 侧摆（外展／内收）'],
  ['ring_pip_joint', '无名指 · 屈伸'],
  ['pinky_abad_joint', '小指 · 侧摆（外展／内收）'],
  ['pinky_pip_joint', '小指 · 屈伸'],
] as const;

type ControlCapabilities = { backend: string; hand_position: boolean; upper_body_playback: boolean; teaching: boolean; reason: string; playback_backend?: string; playback_progress_estimated?: boolean; max_playback_speed?: number; commissioned?: boolean };
type HandPose = { name: string; side: 'left' | 'right'; positions: number[]; requires_confirmation: boolean };
type HandField = 'position' | 'velocity' | 'acceleration' | 'deceleration' | 'effort';
const HAND_FIELDS: Array<{ key: HandField; label: string; min: number; max: number }> = [
  { key: 'position', label: '位置', min: -1, max: 1 },
  { key: 'velocity', label: '速度', min: 0, max: 1 },
  { key: 'acceleration', label: '加速度', min: 0, max: 10 },
  { key: 'deceleration', label: '减速度', min: 0, max: 10 },
  { key: 'effort', label: 'effort', min: -1, max: 1 },
];
const initialHandParameters = () => Array.from({ length: 10 }, () => ({
  position: '0', velocity: '0.1', acceleration: '0', deceleration: '0', effort: '0',
}));
type Motion = { forward: number; lateral: number; angular: number };
type BridgeState = {
  state: string;
  armed: boolean;
  source: string;
  last_command_age_ms: number | null;
  robot_connected: boolean;
  recording_name: string | null;
  recording_frames: number;
  recording_sample_rate_hz: number | null;
  playback_state: PlaybackState;
  playback_name: string | null;
  playback_progress_ms: number;
  playback_duration_ms: number;
  playback_error: string | null;
  control_capabilities?: ControlCapabilities;
  recording_error?: string | null;
  last_hand_command?: string | null;
};
const disconnectedState = (current: BridgeState): BridgeState => {
  const playback = playbackAfterDisconnect(current.playback_state, current.playback_error);
  return {
    ...current,
    armed: false,
    robot_connected: false,
    playback_state: playback.state,
    playback_error: playback.error,
    control_capabilities: undefined,
  };
};
type JoystickValue = { x: number; y: number };
type TrajectoryInfo = { name: string; frames: number; duration_ms: number; arm_joints: number; left_hand_joints: number; right_hand_joints: number };
const DEFAULT_URL = 'ws://10.0.1.41:8765';
const MODES = [
  ['PASSIVE_DEFAULT', '被动'],
  ['DAMPING_DEFAULT', '阻尼'],
  ['JOINT_DEFAULT', '关节'],
  ['STAND_DEFAULT', '站立'],
  ['LOCOMOTION_DEFAULT', '行走'],
] as const;
const AWARD_PRESETS = [
  ['单手抓取', '单手抓取奖状'],
  ['单手携带', '单手携带'],
  ['双手接持', '双手接持'],
  ['双手递出', '双手递出'],
  ['收回双手', '收回双手'],
] as const;

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function HandSlider({ label, value, min, max, unit = '', onChange }: {
  label: string; value: string; min: number; max: number; unit?: string; onChange: (value: string) => void;
}) {
  const parsed = Number(value);
  const valid = value.trim() !== '' && Number.isFinite(parsed);
  const outside = !valid || parsed < min || parsed > max;
  return <View style={styles.sliderField}>
    <View style={styles.sliderLabelRow}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.sliderValue}>{valid ? parsed.toFixed(3) : '无效值'} {unit}</Text>
    </View>
    <Slider style={styles.handSlider} accessibilityLabel={label}
      minimumValue={min} maximumValue={max} step={0.001}
      value={valid ? clamp(parsed, min, max) : min}
      minimumTrackTintColor="#48d597" maximumTrackTintColor="#405264" thumbTintColor="#79e3b5"
      onValueChange={(next) => onChange(String(clamp(Number(next.toFixed(3)), min, max)))} />
    <View style={styles.sliderLabelRow}>
      <Text style={styles.fieldHint}>{min.toFixed(3)}</Text>
      <Text style={styles.fieldHint}>{max.toFixed(3)}</Text>
    </View>
    {outside ? <Text style={styles.handHint}>当前值超出滑条范围，尚未改写；拖动后才更新目标。</Text> : null}
  </View>;
}

function bridgeStateFrom(payload: Partial<BridgeState>, fallback: BridgeState): BridgeState {
  return {
    ...fallback,
    ...payload,
    state: typeof payload.state === 'string' ? payload.state : fallback.state,
    armed: payload.armed ?? fallback.armed,
    robot_connected: payload.robot_connected ?? fallback.robot_connected,
    recording_name: payload.recording_name === undefined ? fallback.recording_name : payload.recording_name,
    playback_state: payload.playback_state ?? fallback.playback_state,
  };
}

function Joystick({
  value,
  onChange,
  label,
}: {
  value: JoystickValue;
  onChange: (value: JoystickValue) => void;
  label: string;
}) {
  const size = 142;
  const radius = 52;
  const knob = 58;
  const updateFromTouch = useCallback(
    (x: number, y: number) => {
      const distance = Math.sqrt(x * x + y * y) || 1;
      const scale = Math.min(1, radius / distance);
      onChange({ x: clamp((x * scale) / radius, -1, 1), y: clamp((-y * scale) / radius, -1, 1) });
    },
    [onChange],
  );
  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponderCapture: () => true,
        onMoveShouldSetPanResponderCapture: () => true,
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => onChange({ x: 0, y: 0 }),
        onPanResponderMove: (_event, gestureState) => updateFromTouch(gestureState.dx, gestureState.dy),
        onPanResponderRelease: () => onChange({ x: 0, y: 0 }),
        onPanResponderTerminate: () => onChange({ x: 0, y: 0 }),
      }),
    [onChange, size, updateFromTouch],
  );
  return (
    <View style={styles.joystickColumn}>
      <Text style={styles.joystickLabel}>{label}</Text>
      <View style={[styles.joystick, { width: size, height: size }]} {...responder.panHandlers}>
        <View
          style={[
            styles.knob,
            {
              width: knob,
              height: knob,
              left: size / 2 - knob / 2 + value.x * radius,
              top: size / 2 - knob / 2 - value.y * radius,
            },
          ]}
        />
      </View>
    </View>
  );
}

export default function App() {
  const { width } = useWindowDimensions();
  const compactLayout = width < 720;
  const [url, setUrl] = useState(DEFAULT_URL);
  const [statusText, setStatusText] = useState('未连接');
  const [bridgeState, setBridgeState] = useState<BridgeState>({
    state: 'IDLE',
    armed: false,
    source: 'mobile_app',
    last_command_age_ms: null,
    robot_connected: false,
    recording_name: null,
    recording_frames: 0,
    recording_sample_rate_hz: null,
    playback_state: 'idle',
    playback_name: null,
    playback_progress_ms: 0,
    playback_duration_ms: 0,
    playback_error: null,
  });
  const [leftStick, setLeftStick] = useState<JoystickValue>({ x: 0, y: 0 });
  const [rightStick, setRightStick] = useState<JoystickValue>({ x: 0, y: 0 });
  const leftStickRef = useRef<JoystickValue>({ x: 0, y: 0 });
  const rightStickRef = useRef<JoystickValue>({ x: 0, y: 0 });
  const [page, setPage] = useState<'move' | 'hand' | 'trajectory'>('move');
  const [handSide, setHandSide] = useState<'left' | 'right'>('left');
  const [handEditor, setHandEditor] = useState<'parameters' | 'positions'>('parameters');
  const [handParameters, setHandParameters] = useState<Record<'left' | 'right', Record<HandField, string>[]>>({
    left: initialHandParameters(), right: initialHandParameters(),
  });
  const [handTargets, setHandTargets] = useState<Record<'left' | 'right', string[]>>({
    left: Array(10).fill('0'), right: Array(10).fill('0'),
  });
  const [handPoses, setHandPoses] = useState<HandPose[]>([]);
  const [handPoseName, setHandPoseName] = useState('');
  const [confirmRelease, setConfirmRelease] = useState(false);
  const canSendHand = bridgeState.control_capabilities?.hand_position === true;
  const canPlay = bridgeState.control_capabilities?.upper_body_playback === true;
  const [trajectories, setTrajectories] = useState<TrajectoryInfo[]>([]);
  const [trajectoryName, setTrajectoryName] = useState('');
  const [sampleRate, setSampleRate] = useState('20');
  const [playbackSpeed, setPlaybackSpeed] = useState('0.25');
  const [selectedTrajectory, setSelectedTrajectory] = useState('');
  const [editingTrajectory, setEditingTrajectory] = useState('');
  const [renameDraft, setRenameDraft] = useState('');
  const recording = bridgeState.recording_name !== null;
  const socketRef = useRef<WebSocket | null>(null);
  const socketTokenRef = useRef(0);
  const sequenceRef = useRef(0);
  const clientIdRef = useRef(`mobile-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const connectedRef = useRef(false);
  const armedRef = useRef(false);
  const lastHeartbeatRef = useRef(0);
  const playbackStateRef = useRef<BridgeState['playback_state']>('idle');
  const motionRef = useRef<Motion>({ forward: 0, lateral: 0, angular: 0 });

  useEffect(() => {
    playbackStateRef.current = bridgeState.playback_state;
    if (bridgeState.recording_name !== null || isPlaybackBusy(bridgeState.playback_state)) {
      leftStickRef.current = { x: 0, y: 0 };
      rightStickRef.current = { x: 0, y: 0 };
      motionRef.current = { forward: 0, lateral: 0, angular: 0 };
      setLeftStick({ x: 0, y: 0 });
      setRightStick({ x: 0, y: 0 });
    }
  }, [bridgeState.playback_state, bridgeState.recording_name]);

  const updateMotion = useCallback((left: JoystickValue, right: JoystickValue) => {
    motionRef.current = { forward: left.y * 0.12, lateral: left.x * 0.08, angular: right.x * 0.15 };
  }, []);

  const send = useCallback((payload: Record<string, unknown>, withSequence = true) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      if (payload.type !== 'velocity' && payload.type !== 'heartbeat') console.warn('[teleop] send skipped: socket not open', payload.type);
      return false;
    }
    const message = withSequence ? { ...payload, sequence: ++sequenceRef.current } : payload;
    if (payload.type !== 'velocity' && payload.type !== 'heartbeat') console.info('[teleop] send', message);
    socket.send(JSON.stringify(message));
    return true;
  }, []);

  const closeConnection = useCallback((notify = true) => {
    const socket = socketRef.current;
    socketTokenRef.current += 1;
    socketRef.current = null;
    connectedRef.current = false;
    armedRef.current = false;
    leftStickRef.current = { x: 0, y: 0 };
    rightStickRef.current = { x: 0, y: 0 };
    motionRef.current = { forward: 0, lateral: 0, angular: 0 };
    setLeftStick({ x: 0, y: 0 });
    setRightStick({ x: 0, y: 0 });
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'arm', enabled: false, sequence: ++sequenceRef.current }));
      socket.close();
    } else {
      socket?.close();
    }
    const playbackWasActive = isPlaybackBusy(playbackStateRef.current);
    setBridgeState(disconnectedState);
    if (notify) setStatusText(playbackWasActive ? '连接断开，MC 停止未确认' : '已断开');
  }, []);

  const connect = useCallback(() => {
    if (!url.trim() || socketRef.current?.readyState === WebSocket.OPEN) return;
    closeConnection(false);
    const token = socketTokenRef.current + 1;
    socketTokenRef.current = token;
    sequenceRef.current = 0;
    setStatusText('连接中…');
    try {
      const socket = new WebSocket(url.trim());
      socketRef.current = socket;
      socket.onopen = () => {
        if (socketTokenRef.current !== token) return;
        connectedRef.current = true;
        lastHeartbeatRef.current = 0;
        setStatusText('已连接');
        send({ type: 'hello', protocol_version: 1, client_id: clientIdRef.current }, false);
      };
      socket.onmessage = (event) => {
        if (socketTokenRef.current !== token) return;
        try {
          const message = JSON.parse(event.data) as {
            type?: string;
            protocol_version?: number;
            capabilities?: string[];
            state?: string | BridgeState;
            armed?: boolean;
            source?: string;
            last_command_age_ms?: number | null;
            robot_connected?: boolean;
            trajectories?: TrajectoryInfo[];
            request_type?: string;
            code?: string;
            message?: string;
          };
          if (message.type === 'hello_ack') {
            console.info('[teleop] bridge hello_ack', message.protocol_version, message.capabilities || []);
          } else if (message.type === 'state') {
            setBridgeState((current) => {
              const next = bridgeStateFrom(message as Partial<BridgeState>, current);
              armedRef.current = next.armed;
              return next;
            });
          } else if (message.type === 'ack' && message.state && typeof message.state === 'object') {
            const payload = message.state as unknown as Partial<BridgeState> & { trajectories?: TrajectoryInfo[] };
            setBridgeState((current) => {
              const next = bridgeStateFrom(payload, current);
              armedRef.current = next.armed;
              return next;
            });
            const handPayload = message.state as Partial<BridgeState> & {
              hand_poses?: HandPose[]; hand_feedback?: Record<'left' | 'right', number[]>;
            };
            if (Array.isArray(handPayload.hand_poses)) setHandPoses(handPayload.hand_poses);
            if (handPayload.hand_feedback) {
              const feedback = handPayload.hand_feedback;
              setHandTargets({ left: feedback.left.map(String), right: feedback.right.map(String) });
            }
            if (message.request_type === 'hand_pose_save') setStatusText('手部预设已保存，尚未执行');
            if (message.request_type === 'hand_pose_apply' || message.request_type === 'hand_positions') setStatusText('手部目标已发送，请观察抓握结果');
            if (Array.isArray(payload.trajectories)) {
              setTrajectories(payload.trajectories);
            }
            if (message.request_type === 'trajectory_record_start') setStatusText(`正在录制：${payload.recording_name || ''}`);
            if (message.request_type === 'trajectory_record_stop') setStatusText('轨迹已保存');
            if (message.request_type === 'trajectory_rename') setStatusText('轨迹名称已修改');
            if (message.request_type === 'trajectory_delete') setStatusText('轨迹已删除');
            if (message.request_type === 'trajectory_play') {
              if (payload.playback_state === 'playing') setStatusText(`正在播放：${payload.playback_name || ''}`);
              if (payload.playback_state === 'paused') setStatusText(`已暂停：${payload.playback_name || ''}`);
              if (payload.playback_state === 'idle') setStatusText('播放已停止');
            }
          } else if (message.type === 'trajectory_list' && Array.isArray(message.trajectories)) {
            setTrajectories(message.trajectories);
          } else if (message.type === 'error') {
            console.warn('[teleop] bridge error', message.code, message.message);
            setStatusText(message.message || message.code || '服务端错误');
            if (message.code === 'busy' || message.code === 'replaced' || message.code === 'not_owner') closeConnection(false);
          }
        } catch {
          setStatusText('收到无效服务消息');
        }
      };
      socket.onerror = () => {
        if (socketTokenRef.current === token) setStatusText('连接错误');
      };
      socket.onclose = () => {
        if (socketTokenRef.current !== token) return;
        socketRef.current = null;
        connectedRef.current = false;
        armedRef.current = false;
        const playbackWasActive = isPlaybackBusy(playbackStateRef.current);
        setBridgeState(disconnectedState);
        setStatusText(playbackWasActive ? '连接断开，MC 停止未确认' : '连接已断开');
      };
    } catch {
      setStatusText('无法创建 WebSocket');
    }
  }, [closeConnection, send, url]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (!connectedRef.current) return;
      if (armedRef.current) {
        send({ type: 'velocity', ...motionRef.current });
      } else if (Date.now() - lastHeartbeatRef.current > 1000) {
        if (send({ type: 'heartbeat' })) lastHeartbeatRef.current = Date.now();
      }
    }, 50);
    return () => clearInterval(timer);
  }, [send]);

  useEffect(() => {
    const onAppStateChange = (nextState: AppStateStatus) => {
      if (nextState !== 'active') closeConnection(false);
    };
    const subscription = AppState.addEventListener('change', onAppStateChange);
    return () => subscription.remove();
  }, [closeConnection]);

  useEffect(() => () => closeConnection(false), [closeConnection]);

  useEffect(() => {
    if (bridgeState.playback_state === 'completed') setStatusText(`播放完成：${bridgeState.playback_name || ''}`);
    if (bridgeState.playback_state === 'stop_failed') setStatusText('停止未确认：请现场使用物理急停');
    if (bridgeState.playback_state === 'error') setStatusText(bridgeState.playback_error || '轨迹播放失败');
  }, [bridgeState.playback_error, bridgeState.playback_name, bridgeState.playback_state]);

  const setLeft = useCallback((value: JoystickValue) => {
    leftStickRef.current = value;
    setLeftStick(value);
    updateMotion(value, rightStickRef.current);
  }, [updateMotion]);
  const setRight = useCallback((value: JoystickValue) => {
    rightStickRef.current = value;
    setRightStick(value);
    updateMotion(leftStickRef.current, value);
  }, [updateMotion]);

  const arm = () => {
    const enabled = !bridgeState.armed;
    if (send({ type: 'arm', enabled })) armedRef.current = enabled;
  };
  const emergencyStop = () => {
    if (send({ type: 'estop' })) {
      armedRef.current = false;
      leftStickRef.current = { x: 0, y: 0 };
      rightStickRef.current = { x: 0, y: 0 };
      setLeftStick({ x: 0, y: 0 });
      setRightStick({ x: 0, y: 0 });
      motionRef.current = { forward: 0, lateral: 0, angular: 0 };
    }
  };

  const sendHandParameters = () => {
    const inputs = handParameters[handSide];
    for (const joint of inputs) {
      for (const field of HAND_FIELDS) {
        const value = Number(joint[field.key]);
        if (!joint[field.key].trim() || !Number.isFinite(value) || value < field.min || value > field.max) {
          setStatusText(`${field.label}需为 ${field.min}～${field.max} 的有限数字`);
          return;
        }
      }
    }
    const joints = inputs.map((joint, index) => ({ index,
      position: Number(joint.position), velocity: Number(joint.velocity),
      acceleration: Number(joint.acceleration), deceleration: Number(joint.deceleration),
      effort: Number(joint.effort),
    }));
    send({ type: 'hand_target', side: handSide, joints });
  };
  const handPositions = (): number[] | null => {
    const inputs = handTargets[handSide];
    const positions = inputs.map(Number);
    if (inputs.some((x) => !x.trim()) || positions.some((x) => !Number.isFinite(x) || x < -Math.PI || x > Math.PI)) {
      setStatusText('位置需为 −π～π rad 的有限数字，空值不会自动转成零');
      return null;
    }
    return positions;
  };
  const sendHandTarget = () => {
    const positions = handPositions();
    if (positions) send({ type: 'hand_positions', side: handSide, positions });
  };
  const saveHandPose = () => {
    const positions = handPositions();
    if (!handPoseName.trim()) { setStatusText('请填写手部预设名称'); return; }
    if (positions) send({ type: 'hand_pose_save', name: handPoseName.trim(), side: handSide,
                         positions, requires_confirmation: confirmRelease });
  };
  const applyHandPose = (pose: HandPose) => {
    if (pose.requires_confirmation) {
      Alert.alert('确认交接', `请确认选手已接稳奖状，再执行“${pose.name}”。`, [
        { text: '取消', style: 'cancel' },
        { text: '已接稳，执行', onPress: () => send({ type: 'hand_pose_apply', name: pose.name, confirmed: true }) },
      ]);
    } else send({ type: 'hand_pose_apply', name: pose.name });
  };
  const refreshTrajectories = useCallback(() => send({ type: 'trajectory_list' }), [send]);
  const startRecording = () => {
    const name = trajectoryName.trim();
    if (!name) {
      setStatusText('请先填写一个不重复的轨迹名称');
      return;
    }
    const rate = Number(sampleRate);
    if (!Number.isFinite(rate) || rate < 1 || rate > 100) {
      setStatusText('采样频率需为 1～100 Hz');
      return;
    }
    send({ type: 'trajectory_record_start', name, sample_rate_hz: rate, teach_mode: false });
  };
  const stopRecording = () => {
    send({ type: 'trajectory_record_stop' });
  };
  const deleteTrajectory = (name: string) => {
    Alert.alert('删除轨迹', `确定删除“${name}”吗？此操作无法撤销。`, [
      { text: '取消', style: 'cancel' },
      { text: '删除', style: 'destructive', onPress: () => {
        if (send({ type: 'trajectory_delete', name })) {
          if (selectedTrajectory === name) setSelectedTrajectory('');
          if (editingTrajectory === name) setEditingTrajectory('');
        }
      } },
    ]);
  };
  const renameTrajectory = (name: string) => {
    const next = renameDraft.trim();
    if (next && next !== name && send({ type: 'trajectory_rename', old_name: name, new_name: next })) {
      if (selectedTrajectory === name) setSelectedTrajectory(next);
      setEditingTrajectory('');
      setRenameDraft('');
      refreshTrajectories();
    }
  };
  const playTrajectory = (name: string) => {
    const speed = Number(playbackSpeed);
    if (!Number.isFinite(speed) || speed < 0.01 || speed > 4) {
      setStatusText('播放速度需为 0.01～4 倍');
      return;
    }
    send({ type: 'trajectory_play', name, speed });
  };
  const controlPlayback = (command: 'pause' | 'resume' | 'stop') => send({ type: 'trajectory_play', command });
  const playbackPercent = bridgeState.playback_duration_ms > 0
    ? Math.min(100, Math.round(bridgeState.playback_progress_ms * 100 / bridgeState.playback_duration_ms))
    : 0;
  const playbackActive = isPlaybackBusy(bridgeState.playback_state);
  const selectedInfo = trajectories.find((trajectory) => trajectory.name === selectedTrajectory);
  const canStartPlayback = bridgeState.robot_connected && bridgeState.armed && canPlay
    && !!selectedInfo && !playbackActive && !recording;
  const playbackLabels: Record<BridgeState['playback_state'], string> = {
    idle: '当前空闲', preparing: '准备中：校验、上传或等待 MC 启动', playing: '正在播放',
    pausing: '暂停中：等待停止反馈', paused: '已暂停并确认保持', stopping: '停止中：等待反馈',
    stop_failed: '停止未确认：保持互锁，请现场使用物理急停', completed: '播放完成', error: '播放失败',
  };

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.header}>
          <View>
            <Text style={styles.title}>X2 MOBILE TELEOP</Text>
            <Text style={styles.subtitle}>PC2 WebSocket 控制台 · 低速安全模式</Text>
          </View>
          <View style={styles.statusPill}>
            <View style={[styles.statusDot, { backgroundColor: bridgeState.robot_connected ? '#48d597' : '#ffb454' }]} />
            <Text style={styles.statusText}>{statusText}</Text>
          </View>
        </View>

        <View style={styles.connectionRow}>
          <TextInput value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} style={styles.urlInput} placeholder="ws://PC2:8765" placeholderTextColor="#6d7885" />
          <Pressable style={styles.connectButton} onPress={connect}><Text style={styles.connectText}>连接</Text></Pressable>
          <Text style={styles.stateText}>{bridgeState.state}{bridgeState.armed ? ' · 已解锁' : ' · 未解锁'}</Text>
        </View>

        <Text style={styles.handHint}>{bridgeState.robot_connected ? bridgeState.control_capabilities?.reason || '正在读取机器人执行能力，执行按钮暂不可用' : '连接后读取执行能力'}</Text>
        {bridgeState.state === 'ESTOP' ? <Pressable style={styles.smallButton} onPress={() => send({ type: 'clear_estop' })}><Text style={styles.smallButtonText}>清除软件急停锁存（不会自动进入 TELEOP）</Text></Pressable> : null}
        <View style={styles.tabs}>
          <Pressable style={[styles.tab, page === 'move' && styles.tabActive]} onPress={() => setPage('move')}><Text style={styles.tabText}>移动控制</Text></Pressable>
          <Pressable style={[styles.tab, page === 'hand' && styles.tabActive]} onPress={() => { setPage('hand'); send({ type: 'hand_pose_list' }); }}><Text style={styles.tabText}>灵巧手预设</Text></Pressable>
          <Pressable style={[styles.tab, page === 'trajectory' && styles.tabActive]} onPress={() => { setPage('trajectory'); refreshTrajectories(); }}><Text style={styles.tabText}>轨迹录制</Text></Pressable>
        </View>

        {page === 'move' ? <View style={[styles.mainRow, compactLayout && styles.mainRowCompact]}>
          <View pointerEvents={playbackActive || recording ? "none" : "auto"} style={styles.controlPanel}>
            <Joystick label="前进 / 后退 / 横移" value={leftStick} onChange={setLeft} />
            <Joystick label="旋转（左 / 右）" value={rightStick} onChange={setRight} />
          </View>
          <View style={[styles.actionPanel, compactLayout && styles.actionPanelCompact]}>
            <View style={styles.armRow}>
              <Pressable style={[styles.armButton, bridgeState.armed && styles.disarmButton]} disabled={!bridgeState.armed && playbackActive} onPress={arm}>
                <Text style={styles.armText}>{bridgeState.armed ? '退出 TELEOP' : '进入 TELEOP'}</Text>
              </Pressable>
              <Pressable style={styles.estopButton} onPress={emergencyStop}><Text style={styles.estopText}>急停</Text></Pressable>
            </View>
            <Text style={styles.sectionLabel}>运动模式</Text>
            <View style={styles.buttonGrid}>
              {MODES.map(([mode, label]) => <Pressable key={mode} disabled={playbackActive || recording} style={[styles.modeButton, (playbackActive || recording) && styles.disabledButton]} onPress={() => send({ type: 'mode', mode })}><Text style={styles.modeText}>{label}</Text></Pressable>)}
            </View>
            <Text style={styles.sectionLabel}>携带奖状</Text>
            <Text style={styles.handHint}>抓稳后由人工使用官方行走；此阶段不播放或保持手臂轨迹。松手请到灵巧手预设页确认执行。</Text>
            <Pressable style={styles.presetButton} onPress={() => { setPage('hand'); send({ type: 'hand_pose_list' }); }}><Text style={styles.modeText}>选择手部抓握预设</Text></Pressable>
            <Text style={styles.hint}>松开摇杆发送零速度；切后台或断网会请求停止。MC 动画须确认停止反馈，通信中断时使用现场物理急停。</Text>
          </View>
        </View> : page === 'hand' ? <View style={styles.handPanel}>
          <View style={styles.armRow}>
            <Pressable style={[styles.armButton, bridgeState.armed && styles.disarmButton]} disabled={!bridgeState.armed && playbackActive} onPress={arm}>
              <Text style={styles.armText}>{bridgeState.armed ? '退出 TELEOP' : '进入 TELEOP'}</Text>
            </Pressable>
            <Pressable style={styles.estopButton} onPress={emergencyStop}><Text style={styles.estopText}>急停</Text></Pressable>
          </View>
          <View style={styles.sideSwitch}>
            {(['left', 'right'] as const).map((side) => <Pressable key={side} style={[styles.sideButton, handSide === side && styles.sideButtonActive]} onPress={() => setHandSide(side)}><Text style={styles.sideText}>{side === 'left' ? '左手' : '右手'}</Text></Pressable>)}
          </View>
          <View style={styles.sideSwitch}>
            {(['parameters', 'positions'] as const).map((editor) => <Pressable key={editor} style={[styles.sideButton, handEditor === editor && styles.sideButtonActive]} onPress={() => setHandEditor(editor)}><Text style={styles.sideText}>{editor === 'parameters' ? '旧版五参数' : '反馈位置与预设'}</Text></Pressable>)}
          </View>
          {handEditor === 'parameters' ? <>
            <Text style={styles.handHint}>恢复原版参数与发送方式：左手前三槽位置由程序取反，其余原样发送。以下为输入范围，不是硬件限位；effort 等字段是否生效由手部固件决定，不代表已支持力控或卸力。初始零值不是已标定的张开姿态。</Text>
            <Text style={styles.handHint}>拖动只编辑目标，点击下方发送按钮才驱动所选手。每槽对应一个主动自由度，其他耦合关节随动。</Text>
              {handParameters[handSide].map((joint, index) => <View key={index} style={styles.handJointCard}>
                <Text style={styles.trajectoryTitle}>{handSide === 'left' ? '左手' : '右手'} · {HAND_JOINTS[index][1]}</Text>
                <Text style={styles.fieldHint}>槽 {index} · {HAND_JOINTS[index][0]}</Text>
                {HAND_FIELDS.map((field) => <HandSlider key={field.key}
                  label={`${HAND_JOINTS[index][1]} · ${field.label}`} value={joint[field.key]}
                  min={field.min} max={field.max} unit={field.key === 'position' ? 'rad' : ''}
                  onChange={(value) => setHandParameters((current) => ({
                    ...current, [handSide]: current[handSide].map((item, i) => i === index ? { ...item, [field.key]: value } : item),
                  }))} />)}
              </View>)}
            <Pressable disabled={!canSendHand || !bridgeState.armed || playbackActive} style={[styles.sendHandButton, (!canSendHand || !bridgeState.armed || playbackActive) && styles.disabledButton]} onPress={sendHandParameters}><Text style={styles.armText}>发送所选手的参数目标</Text></Pressable>
            <Text style={styles.fieldHint}>{bridgeState.last_hand_command || '尚未发送手部目标'}</Text>
          </> : <>
          <Text style={styles.handHint}>位置单位 rad，直接使用反馈符号，不做左手取反。±π rad 是编辑范围，不是硬件安全限位。此处的预设只保存位置。</Text>
          <Pressable style={styles.smallButton} onPress={() => send({ type: 'hand_state' })}><Text style={styles.smallButtonText}>读取当前双手位置</Text></Pressable>
          <Text style={styles.handHint}>拖动只编辑目标，读取和载入预设也不会自动发送。</Text>
          {handTargets[handSide].map((position, index) => <View key={index} style={styles.handJointCard}>
            <Text style={styles.trajectoryTitle}>{handSide === 'left' ? '左手' : '右手'} · {HAND_JOINTS[index][1]}</Text>
            <Text style={styles.fieldHint}>槽 {index} · {HAND_JOINTS[index][0]}</Text>
            <HandSlider label={`${HAND_JOINTS[index][1]} · 位置`} value={position} min={-Math.PI} max={Math.PI} unit="rad"
              onChange={(value) => setHandTargets((current) => ({ ...current,
              [handSide]: current[handSide].map((x, i) => i === index ? value : x),
            }))} />
          </View>)}
          <Pressable disabled={!canSendHand || !bridgeState.armed || playbackActive} style={[styles.sendHandButton, (!canSendHand || !bridgeState.armed || playbackActive) && styles.disabledButton]} onPress={sendHandTarget}><Text style={styles.armText}>发送所选手的位置目标</Text></Pressable>
          <Text style={styles.sectionLabel}>保存手部预设</Text>
          <TextInput value={handPoseName} onChangeText={setHandPoseName} style={styles.urlInput} placeholder="例如：右手抓奖状 / 右手松开奖状" placeholderTextColor="#6d7885" />
          <Pressable style={styles.smallButton} onPress={() => setConfirmRelease(!confirmRelease)}><Text style={styles.smallButtonText}>{confirmRelease ? '☑' : '☐'} 执行前确认选手已接稳（松手预设请勾选）</Text></Pressable>
          <View style={styles.trajectoryActions}>
            <Pressable style={styles.smallButton} onPress={saveHandPose}><Text style={styles.smallButtonText}>保存新预设</Text></Pressable>
            <Pressable style={styles.smallButton} onPress={() => send({ type: 'hand_pose_list' })}><Text style={styles.smallButtonText}>刷新预设</Text></Pressable>
          </View>
          <Text style={styles.fieldHint}>{bridgeState.last_hand_command || '尚未发送手部目标'}</Text>
          {handPoses.map((pose) => <View key={pose.name} style={styles.trajectoryRow}>
            <Text style={styles.trajectoryTitle}>{pose.name} · {pose.side === 'left' ? '左手' : '右手'}{pose.requires_confirmation ? ' · 交接确认' : ''}</Text>
            <Pressable style={styles.smallButton} onPress={() => { setHandSide(pose.side); setHandTargets((current) => ({ ...current, [pose.side]: pose.positions.map(String) })); setConfirmRelease(pose.requires_confirmation); }}><Text style={styles.smallButtonText}>载入编辑</Text></Pressable>
            <Pressable disabled={!canSendHand || !bridgeState.armed || playbackActive} style={[styles.smallButton, (!canSendHand || !bridgeState.armed || playbackActive) && styles.disabledButton]} onPress={() => applyHandPose(pose)}><Text style={styles.smallButtonText}>执行</Text></Pressable>
            <Pressable style={styles.smallButton} onPress={() => send({ type: 'hand_pose_rename', name: pose.name, new_name: handPoseName.trim() })}><Text style={styles.smallButtonText}>改为输入名称</Text></Pressable>
            <Pressable style={styles.deleteButton} onPress={() => Alert.alert('删除手部预设', `删除“${pose.name}”？`, [{ text: '取消' }, { text: '删除', style: 'destructive', onPress: () => send({ type: 'hand_pose_delete', name: pose.name }) }])}><Text style={styles.smallButtonText}>删除</Text></Pressable>
          </View>)}
          </>}
        </View> : <View style={styles.handPanel}>
          <View style={styles.armRow}>
            <Pressable style={[styles.armButton, bridgeState.armed && styles.disarmButton]} disabled={!bridgeState.armed && playbackActive} onPress={arm}><Text style={styles.armText}>{bridgeState.armed ? '退出 TELEOP' : '进入 TELEOP'}</Text></Pressable>
            <Pressable style={styles.estopButton} onPress={emergencyStop}><Text style={styles.estopText}>急停</Text></Pressable>
          </View>
          <Text style={styles.sectionLabel}>轨迹名称</Text>
          <TextInput value={trajectoryName} onChangeText={setTrajectoryName} editable={!recording} style={styles.urlInput} placeholder="必须填写，例如：单手抓取" placeholderTextColor="#6d7885" />
          <Text style={styles.sectionLabel}>上半身采样频率（Hz）</Text>
          <TextInput value={sampleRate} onChangeText={setSampleRate} style={styles.rateInput} keyboardType="numeric" placeholder="20" placeholderTextColor="#6d7885" />
          <Text style={styles.sectionLabel}>播放速度</Text>
          <View style={styles.speedRow}>{(['0.25', '0.5', '1', '2'] as const).map((speed) => <Pressable key={speed} disabled={playbackActive || Number(speed) > (bridgeState.control_capabilities?.max_playback_speed ?? 4)} style={[(playbackActive || Number(speed) > (bridgeState.control_capabilities?.max_playback_speed ?? 4)) && styles.disabledButton, styles.speedButton, playbackSpeed === speed && styles.speedButtonActive]} onPress={() => setPlaybackSpeed(speed)}><Text style={styles.smallButtonText}>{speed}x</Text></Pressable>)}</View>
          <View style={styles.trajectoryActions}>
            <Pressable disabled={!recording && playbackActive} style={[styles.recordButton, recording && styles.stopRecordButton, !recording && playbackActive && styles.disabledButton]} onPress={recording ? stopRecording : startRecording}><Text style={styles.armText}>{recording ? '停止并保存录制' : '开始上肢状态录制'}</Text></Pressable>
            <Pressable style={styles.connectButton} onPress={refreshTrajectories}><Text style={styles.connectText}>刷新列表</Text></Pressable>
          </View>
          <View style={styles.trajectoryActions}>
            <Pressable disabled={!canStartPlayback} style={[styles.recordButton, !canStartPlayback && styles.disabledButton]} onPress={() => selectedInfo && playTrajectory(selectedInfo.name)}><Text style={styles.armText}>{selectedInfo ? `播放：${selectedInfo.name}` : '请先选择轨迹'}</Text></Pressable>
          </View>
          <View style={styles.trajectoryActions}>
            <Pressable style={[styles.smallButton, bridgeState.playback_state !== 'playing' && styles.disabledButton]} disabled={bridgeState.playback_state !== 'playing'} onPress={() => controlPlayback('pause')}><Text style={styles.smallButtonText}>暂停</Text></Pressable>
            <Pressable style={[styles.smallButton, bridgeState.playback_state !== 'paused' && styles.disabledButton]} disabled={bridgeState.playback_state !== 'paused'} onPress={() => controlPlayback('resume')}><Text style={styles.smallButtonText}>继续</Text></Pressable>
            <Pressable disabled={!playbackActive || !bridgeState.robot_connected} style={[styles.deleteButton, (!playbackActive || !bridgeState.robot_connected) && styles.disabledButton]} onPress={() => controlPlayback('stop')}><Text style={styles.smallButtonText}>{bridgeState.playback_state === 'stop_failed' ? '重试停止' : '停止'}</Text></Pressable>
          </View>
          <View style={styles.activityPanel}>
            <Text style={styles.activityTitle}>{recording ? `● 正在录制：${bridgeState.recording_name}` : `${playbackLabels[bridgeState.playback_state]} ${bridgeState.playback_name || ''}`}</Text>
            {bridgeState.playback_error ? <Text style={styles.handHint}>{bridgeState.playback_error}</Text> : null}
            <Text style={styles.fieldHint}>{recording ? `${bridgeState.recording_frames} 帧 · ${bridgeState.recording_sample_rate_hz ?? sampleRate} Hz` : `${bridgeState.control_capabilities?.playback_progress_estimated ? '估算进度 · ' : ''}${bridgeState.playback_progress_ms} / ${bridgeState.playback_duration_ms} ms · ${playbackPercent}%`}</Text>
            <View style={styles.progressTrack}><View style={[styles.progressValue, { width: `${recording ? 100 : playbackPercent}%` }]} /></View>
          </View>
          {bridgeState.recording_error ? <Text style={styles.handHint}>{bridgeState.recording_error}</Text> : null}
          {!canPlay ? <Text style={styles.playbackUnavailable}>真机回放未开放：{bridgeState.control_capabilities?.reason || '尚未加载完整现场验收配置'}</Text> : !bridgeState.armed ? <Text style={styles.handHint}>进入 TELEOP 后才可播放所选轨迹。</Text> : null}
          {canPlay && bridgeState.control_capabilities?.commissioned === false ? <Text style={styles.playbackUnavailable}>现场测试模式：未校验 commissioning 报告。仅限空载、物理急停有人值守时使用。</Text> : null}
          <Text style={styles.sectionLabel}>颁奖动作预设</Text>
          <View style={styles.awardGrid}>{AWARD_PRESETS.map(([name, label]) => { const exists = trajectories.some((item) => item.name === name); const carry = name === '单手携带'; const disabled = carry ? playbackActive || recording : !canPlay || !exists || playbackActive || recording; return <Pressable key={name} disabled={disabled} style={[styles.awardButton, disabled && styles.disabledButton]} onPress={() => carry ? setPage('move') : playTrajectory(name)}><Text style={styles.modeText}>{carry ? '单手携带 · 人工行走' : label}{!carry && !exists ? ' · 未录制' : ''}</Text></Pressable>; })}</View>
          <Text style={styles.handHint}>{recording ? '只采集上肢和双手实际反馈，不改变电机状态。用官方支持的工具制作动作，不强掰关节。' : '同名轨迹不会再覆盖；每条轨迹可独立播放、改名和删除。'}</Text>
          {trajectories.length === 0 ? <Text style={styles.emptyText}>暂无轨迹</Text> : trajectories.map((trajectory) => <View key={trajectory.name} style={[styles.trajectoryRow, selectedTrajectory === trajectory.name && styles.trajectoryRowSelected]}>
            <View style={styles.trajectoryMeta}><Text style={styles.trajectoryTitle}>{trajectory.name}</Text><Text style={styles.fieldHint}>{trajectory.frames} 帧 · {(trajectory.duration_ms / 1000).toFixed(1)} 秒 · 臂 {trajectory.arm_joints} / 左手 {trajectory.left_hand_joints} / 右手 {trajectory.right_hand_joints}</Text></View>
            <Pressable disabled={playbackActive || recording} style={[styles.smallButton, (playbackActive || recording) && styles.disabledButton]} onPress={() => setSelectedTrajectory(trajectory.name)}><Text style={styles.smallButtonText}>{selectedTrajectory === trajectory.name ? '已选择' : '选择'}</Text></Pressable>
            <Pressable disabled={playbackActive || recording} style={[styles.smallButton, (playbackActive || recording) && styles.disabledButton]} onPress={() => { setEditingTrajectory(trajectory.name); setRenameDraft(trajectory.name); }}><Text style={styles.smallButtonText}>改名</Text></Pressable>
            <Pressable disabled={playbackActive || recording} style={[styles.deleteButton, (playbackActive || recording) && styles.disabledButton]} onPress={() => deleteTrajectory(trajectory.name)}><Text style={styles.smallButtonText}>删除</Text></Pressable>
            {editingTrajectory === trajectory.name && <View style={styles.renameRow}><TextInput value={renameDraft} onChangeText={setRenameDraft} style={styles.renameInput} /><Pressable style={styles.smallButton} onPress={() => renameTrajectory(trajectory.name)}><Text style={styles.smallButtonText}>保存</Text></Pressable></View>}
          </View>)}
        </View>}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0b1118' },
  container: { flexGrow: 1, paddingHorizontal: 20, paddingVertical: 16 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: '#f3f7fb', fontSize: 23, fontWeight: '800', letterSpacing: 0 },
  subtitle: { color: '#82909d', marginTop: 3, fontSize: 12 },
  statusPill: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#141e28', borderRadius: 18, paddingHorizontal: 14, paddingVertical: 9 },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  statusText: { color: '#d8e0e8', fontSize: 13 },
  connectionRow: { flexDirection: 'row', alignItems: 'center', marginTop: 14, gap: 8 },
  urlInput: { flex: 1, color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 9, fontSize: 13 },
  rateInput: { color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 9, fontSize: 13, width: 120 },
  connectButton: { backgroundColor: '#295d8a', paddingHorizontal: 18, paddingVertical: 10, borderRadius: 8 },
  connectText: { color: '#fff', fontWeight: '700' },
  stateText: { color: '#9fabb8', minWidth: 128, textAlign: 'right', fontSize: 13 },
  tabs: { flexDirection: 'row', marginTop: 16, borderBottomWidth: 1, borderBottomColor: '#243341' },
  tab: { paddingHorizontal: 18, paddingVertical: 11 },
  tabActive: { borderBottomWidth: 2, borderBottomColor: '#62b4e6' },
  tabText: { color: '#dce7ef', fontWeight: '700', fontSize: 14 },
  mainRow: { flexDirection: 'row', marginTop: 16, gap: 16, minHeight: 390 },
  mainRowCompact: { flexDirection: 'column', minHeight: 0 },
  controlPanel: { flex: 0.95, backgroundColor: '#101923', borderColor: '#1d2a37', borderWidth: 1, borderRadius: 12, flexDirection: 'row', justifyContent: 'space-evenly', alignItems: 'center', minHeight: 236, paddingVertical: 18 },
  joystickColumn: { alignItems: 'center' },
  joystickLabel: { color: '#8f9dab', fontSize: 12, marginBottom: 10 },
  joystick: { backgroundColor: '#172431', borderRadius: 100, borderWidth: 1, borderColor: '#2b4154' },
  knob: { position: 'absolute', borderRadius: 100, backgroundColor: '#387fae', borderWidth: 5, borderColor: '#5ca9d2' },
  actionPanel: { flex: 1.4, backgroundColor: '#101923', borderColor: '#1d2a37', borderWidth: 1, borderRadius: 12, padding: 16 },
  actionPanelCompact: { flex: 0, padding: 14 },
  armRow: { flexDirection: 'row', gap: 10 },
  armButton: { flex: 1, backgroundColor: '#267558', borderRadius: 9, alignItems: 'center', justifyContent: 'center', minHeight: 46 },
  disarmButton: { backgroundColor: '#8d6330' },
  armText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  estopButton: { backgroundColor: '#b93642', borderRadius: 9, width: 105, alignItems: 'center', justifyContent: 'center' },
  estopText: { color: '#fff', fontWeight: '900', fontSize: 17 },
  sectionLabel: { color: '#8392a0', fontSize: 12, marginTop: 17, marginBottom: 8 },
  buttonGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  modeButton: { backgroundColor: '#1a2e3f', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 11, minWidth: 76, alignItems: 'center', flexGrow: 1 },
  presetButton: { backgroundColor: '#332b48', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 11, minWidth: 118, alignItems: 'center', flexGrow: 1 },
  modeText: { color: '#dce7ef', fontSize: 13, fontWeight: '600' },
  hint: { color: '#667684', fontSize: 11, marginTop: 'auto', paddingTop: 12 },
  handPanel: { marginTop: 16, backgroundColor: '#101923', borderColor: '#1d2a37', borderWidth: 1, borderRadius: 12, padding: 16 },
  handJointCard: { paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: '#273644' },
  sliderField: { marginTop: 10 },
  sliderLabelRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  sliderValue: { color: '#e7edf3', fontVariant: ['tabular-nums'], fontSize: 13 },
  handSlider: { width: '100%', height: 44 },
  sideSwitch: { flexDirection: 'row', gap: 8 },
  sideButton: { flex: 1, backgroundColor: '#1a2e3f', paddingVertical: 11, alignItems: 'center', borderRadius: 8 },
  sideButtonActive: { backgroundColor: '#295d8a' },
  sideText: { color: '#fff', fontWeight: '700' },
  handHint: { color: '#8796a3', fontSize: 12, marginVertical: 12 },
  playbackUnavailable: { color: '#ffb454', fontSize: 12, marginVertical: 12 },
  jointRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginBottom: 10 },
  jointName: { color: '#c9d5df', width: 58, fontSize: 12, paddingTop: 22 },
  fieldCell: { flex: 1, minWidth: 0 },
  fieldLabel: { color: '#9eafbd', fontSize: 10, marginBottom: 3 },
  fieldHint: { color: '#687987', fontSize: 10, marginTop: 2 },
  jointInput: { flex: 1, minWidth: 0, color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 7, paddingHorizontal: 8, paddingVertical: 8, fontSize: 12 },
  sendHandButton: { backgroundColor: '#267558', borderRadius: 8, alignItems: 'center', justifyContent: 'center', minHeight: 44, marginTop: 8 },
  trajectoryActions: { flexDirection: 'row', gap: 8, marginTop: 10 },
  speedRow: { flexDirection: 'row', gap: 8, marginBottom: 4 },
  speedButton: { backgroundColor: '#1a2e3f', borderRadius: 6, paddingHorizontal: 14, paddingVertical: 9 },
  speedButtonActive: { backgroundColor: '#295d8a' },
  awardGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  awardButton: { backgroundColor: '#3b3158', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 12, minWidth: 130, alignItems: 'center', flexGrow: 1 },
  disabledButton: { opacity: 0.35 },
  activityPanel: { marginTop: 12, backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 8, padding: 12 },
  activityTitle: { color: '#dce7ef', fontWeight: '700', fontSize: 13 },
  progressTrack: { height: 5, backgroundColor: '#243341', borderRadius: 3, marginTop: 8, overflow: 'hidden' },
  progressValue: { height: 5, backgroundColor: '#48d597', borderRadius: 3 },
  recordButton: { flex: 1, backgroundColor: '#267558', borderRadius: 8, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  stopRecordButton: { backgroundColor: '#8d6330' },
  emptyText: { color: '#687987', textAlign: 'center', paddingVertical: 28 },
  trajectoryRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 7, borderTopWidth: 1, borderTopColor: '#22303c', paddingVertical: 10 },
  trajectoryRowSelected: { backgroundColor: '#14283a' },
  trajectoryMeta: { flex: 1 },
  trajectoryTitle: { color: '#e7edf3', fontWeight: '700', fontSize: 14 },
  smallButton: { backgroundColor: '#295d8a', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 8 },
  deleteButton: { backgroundColor: '#8d3e48', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 8 },
  smallButtonText: { color: '#fff', fontSize: 11, fontWeight: '700' },
  renameRow: { width: '100%', flexDirection: 'row', gap: 8, marginTop: 4 },
  renameInput: { flex: 1, color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 7, paddingHorizontal: 10, paddingVertical: 7, fontSize: 12 },
});
