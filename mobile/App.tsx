import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
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

type Motion = { forward: number; lateral: number; angular: number };
type BridgeState = {
  state: string;
  armed: boolean;
  source: string;
  last_command_age_ms: number | null;
  robot_connected: boolean;
};
type JoystickValue = { x: number; y: number };

const DEFAULT_URL = 'ws://10.0.1.41:8765';
const MODES = [
  ['PASSIVE_DEFAULT', '被动'],
  ['DAMPING_DEFAULT', '阻尼'],
  ['JOINT_DEFAULT', '关节'],
  ['STAND_DEFAULT', '站立'],
  ['LOCOMOTION_DEFAULT', '行走'],
] as const;
const PRESETS = [
  ['grip', '握紧'],
  ['open', '张开'],
  ['victory', '比个耶'],
  ['thumbs_up', '点个赞'],
] as const;

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
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
  });
  const [leftStick, setLeftStick] = useState<JoystickValue>({ x: 0, y: 0 });
  const [rightStick, setRightStick] = useState<JoystickValue>({ x: 0, y: 0 });
  const leftStickRef = useRef<JoystickValue>({ x: 0, y: 0 });
  const rightStickRef = useRef<JoystickValue>({ x: 0, y: 0 });
  const [page, setPage] = useState<'move' | 'hand'>('move');
  const [handSide, setHandSide] = useState<'left' | 'right'>('left');
  const [handTargets, setHandTargets] = useState<Record<'left' | 'right', Array<{ position: string; velocity: string; acceleration: string; deceleration: string; effort: string }>>>({
    left: Array.from({ length: 10 }, () => ({ position: '0', velocity: '0.1', acceleration: '0', deceleration: '0', effort: '0' })),
    right: Array.from({ length: 10 }, () => ({ position: '0', velocity: '0.1', acceleration: '0', deceleration: '0', effort: '0' })),
  });
  const socketRef = useRef<WebSocket | null>(null);
  const socketTokenRef = useRef(0);
  const sequenceRef = useRef(0);
  const clientIdRef = useRef(`mobile-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const connectedRef = useRef(false);
  const armedRef = useRef(false);
  const lastHeartbeatRef = useRef(0);
  const motionRef = useRef<Motion>({ forward: 0, lateral: 0, angular: 0 });

  const updateMotion = useCallback((left: JoystickValue, right: JoystickValue) => {
    motionRef.current = { forward: left.y * 0.12, lateral: left.x * 0.08, angular: right.x * 0.15 };
  }, []);

  const send = useCallback((payload: Record<string, unknown>, withSequence = true) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    const message = withSequence ? { ...payload, sequence: ++sequenceRef.current } : payload;
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
    setBridgeState((current) => ({ ...current, armed: false, robot_connected: false }));
    if (notify) setStatusText('已断开');
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
            state?: string | BridgeState;
            armed?: boolean;
            source?: string;
            last_command_age_ms?: number | null;
            robot_connected?: boolean;
            code?: string;
            message?: string;
          };
          if (message.type === 'state') {
            const next: BridgeState = {
              state: typeof message.state === 'string' ? message.state : 'IDLE',
              armed: Boolean(message.armed),
              source: message.source || 'mobile_app',
              last_command_age_ms: message.last_command_age_ms ?? null,
              robot_connected: Boolean(message.robot_connected),
            };
            setBridgeState(next);
            armedRef.current = next.armed;
          } else if (message.type === 'ack' && message.state && typeof message.state === 'object') {
            setBridgeState(message.state);
            armedRef.current = message.state.armed;
          } else if (message.type === 'error') {
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
        setBridgeState((current) => ({ ...current, armed: false, robot_connected: false }));
        setStatusText('连接已断开');
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

  const updateHandTarget = (index: number, key: 'position' | 'velocity' | 'acceleration' | 'deceleration' | 'effort', value: string) => {
    setHandTargets((current) => ({
      ...current,
      [handSide]: current[handSide].map((joint, jointIndex) => jointIndex === index ? { ...joint, [key]: value } : joint),
    }));
  };

  const sendHandTarget = () => {
    const joints = handTargets[handSide].map((joint, index) => ({
      index,
      position: Number(joint.position) || 0,
      velocity: Number(joint.velocity) || 0,
      acceleration: Number(joint.acceleration) || 0,
      deceleration: Number(joint.deceleration) || 0,
      effort: Number(joint.effort) || 0,
    }));
    send({ type: 'hand_target', side: handSide, joints });
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

        <View style={styles.tabs}>
          <Pressable style={[styles.tab, page === 'move' && styles.tabActive]} onPress={() => setPage('move')}><Text style={styles.tabText}>移动控制</Text></Pressable>
          <Pressable style={[styles.tab, page === 'hand' && styles.tabActive]} onPress={() => setPage('hand')}><Text style={styles.tabText}>灵巧手参数</Text></Pressable>
        </View>

        {page === 'move' ? <View style={[styles.mainRow, compactLayout && styles.mainRowCompact]}>
          <View style={styles.controlPanel}>
            <Joystick label="前进 / 后退 / 横移" value={leftStick} onChange={setLeft} />
            <Joystick label="旋转（左 / 右）" value={rightStick} onChange={setRight} />
          </View>
          <View style={[styles.actionPanel, compactLayout && styles.actionPanelCompact]}>
            <View style={styles.armRow}>
              <Pressable style={[styles.armButton, bridgeState.armed && styles.disarmButton]} onPress={arm}>
                <Text style={styles.armText}>{bridgeState.armed ? '退出 TELEOP' : '进入 TELEOP'}</Text>
              </Pressable>
              <Pressable style={styles.estopButton} onPress={emergencyStop}><Text style={styles.estopText}>急停</Text></Pressable>
            </View>
            <Text style={styles.sectionLabel}>运动模式</Text>
            <View style={styles.buttonGrid}>
              {MODES.map(([mode, label]) => <Pressable key={mode} style={styles.modeButton} onPress={() => send({ type: 'mode', mode })}><Text style={styles.modeText}>{label}</Text></Pressable>)}
            </View>
            <Text style={styles.sectionLabel}>手部预设</Text>
            <View style={styles.buttonGrid}>
              {PRESETS.map(([action, label]) => <Pressable key={action} style={styles.presetButton} onPress={() => send({ type: 'preset', action })}><Text style={styles.modeText}>{label}</Text></Pressable>)}
            </View>
            <Text style={styles.hint}>松开摇杆立即发零速度；切后台或断网会自动停车。</Text>
          </View>
        </View> : <View style={styles.handPanel}>
          <View style={styles.armRow}>
            <Pressable style={[styles.armButton, bridgeState.armed && styles.disarmButton]} onPress={arm}>
              <Text style={styles.armText}>{bridgeState.armed ? '退出 TELEOP' : '进入 TELEOP'}</Text>
            </Pressable>
            <Pressable style={styles.estopButton} onPress={emergencyStop}><Text style={styles.estopText}>急停</Text></Pressable>
          </View>
          <View style={styles.sideSwitch}>
            {(['left', 'right'] as const).map((side) => <Pressable key={side} style={[styles.sideButton, handSide === side && styles.sideButtonActive]} onPress={() => setHandSide(side)}><Text style={styles.sideText}>{side === 'left' ? '左手' : '右手'}</Text></Pressable>)}
          </View>
          <Text style={styles.handHint}>每只手 10 个命令槽。填写位置、速度和力度后发送单帧。</Text>
          {handTargets[handSide].map((joint, index) => <View key={index} style={styles.jointRow}>
            <Text style={styles.jointName}>关节 {index + 1}</Text>
            {(['position', 'velocity', 'acceleration', 'deceleration', 'effort'] as const).map((key) => <TextInput key={key} value={joint[key]} onChangeText={(value) => updateHandTarget(index, key, value)} keyboardType="numeric" style={styles.jointInput} placeholder={key === 'position' ? '位置' : key === 'velocity' ? '速度' : key === 'acceleration' ? '加速度' : key === 'deceleration' ? '减速度' : '力度'} placeholderTextColor="#6d7885" />)}
          </View>)}
          <Pressable style={styles.sendHandButton} onPress={sendHandTarget}><Text style={styles.armText}>发送单帧</Text></Pressable>
        </View>}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0b1118' },
  container: { flexGrow: 1, paddingHorizontal: 20, paddingVertical: 16 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: '#f3f7fb', fontSize: 23, fontWeight: '800', letterSpacing: 1.6 },
  subtitle: { color: '#82909d', marginTop: 3, fontSize: 12 },
  statusPill: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#141e28', borderRadius: 18, paddingHorizontal: 14, paddingVertical: 9 },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 8 },
  statusText: { color: '#d8e0e8', fontSize: 13 },
  connectionRow: { flexDirection: 'row', alignItems: 'center', marginTop: 14, gap: 8 },
  urlInput: { flex: 1, color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 9, fontSize: 13 },
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
  sideSwitch: { flexDirection: 'row', gap: 8 },
  sideButton: { flex: 1, backgroundColor: '#1a2e3f', paddingVertical: 11, alignItems: 'center', borderRadius: 8 },
  sideButtonActive: { backgroundColor: '#295d8a' },
  sideText: { color: '#fff', fontWeight: '700' },
  handHint: { color: '#8796a3', fontSize: 12, marginVertical: 12 },
  jointRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  jointName: { color: '#c9d5df', width: 58, fontSize: 12 },
  jointInput: { flex: 1, minWidth: 0, color: '#e7edf3', backgroundColor: '#111a23', borderColor: '#273644', borderWidth: 1, borderRadius: 7, paddingHorizontal: 8, paddingVertical: 8, fontSize: 12 },
  sendHandButton: { backgroundColor: '#267558', borderRadius: 8, alignItems: 'center', justifyContent: 'center', minHeight: 44, marginTop: 8 },
});
