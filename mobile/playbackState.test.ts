import assert from 'node:assert/strict';
import test from 'node:test';

import { isPlaybackBusy, playbackAfterDisconnect, PLAYBACK_BUSY_STATES } from './playbackState.ts';

test('disconnect converts every active playback state to stop_failed', () => {
  for (const state of PLAYBACK_BUSY_STATES) {
    const result = playbackAfterDisconnect(state, null);
    assert.equal(result.state, 'stop_failed');
    assert.match(result.error || '', /无法确认 MC 已停止/);
  }
});

test('disconnect preserves terminal states that already confirmed motion outcome', () => {
  for (const state of ['idle', 'completed', 'error'] as const) {
    assert.equal(isPlaybackBusy(state), false);
    assert.deepEqual(playbackAfterDisconnect(state, 'existing'), { state, error: 'existing' });
  }
});
