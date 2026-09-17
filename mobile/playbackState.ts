export type PlaybackState =
  | 'idle'
  | 'preparing'
  | 'playing'
  | 'pausing'
  | 'paused'
  | 'stopping'
  | 'stop_failed'
  | 'completed'
  | 'error';

export const PLAYBACK_BUSY_STATES: PlaybackState[] = [
  'preparing', 'playing', 'pausing', 'paused', 'stopping', 'stop_failed',
];

export const DISCONNECTED_PLAYBACK_ERROR =
  '连接已断开，App 无法确认 MC 已停止；重新连接核对状态，必要时使用物理急停';

export function isPlaybackBusy(state: PlaybackState): boolean {
  return PLAYBACK_BUSY_STATES.includes(state);
}

export function playbackAfterDisconnect(state: PlaybackState, error: string | null) {
  return isPlaybackBusy(state)
    ? { state: 'stop_failed' as const, error: DISCONNECTED_PLAYBACK_ERROR }
    : { state, error };
}
