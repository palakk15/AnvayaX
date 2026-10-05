import test from 'node:test';
import assert from 'node:assert/strict';
import { futureTargetTimes, formatIST, modelWindow } from '../site/live_view.js';

test('future horizon excludes past provider hours and uses IST', () => {
  const rows = ['2026-09-28T00:00:00Z','2026-09-28T01:00:00Z','2026-09-28T02:00:00Z','2026-09-28T03:00:00Z']
    .map(valid_time => ({ valid_time, model: 'gfs', precipitation: 1 }));
  const times = futureTargetTimes(rows, Date.parse('2026-09-28T01:30:00Z'), 2);
  assert.deepEqual(times,['2026-09-28T02:00:00Z','2026-09-28T03:00:00Z']);
  assert.match(formatIST(times[0]), /7:30|07:30/);
  assert.match(formatIST(times[0]), /IST$/);
});

test('model window does not invent missing source values', () => {
  const times = ['2026-09-28T02:00:00Z','2026-09-28T03:00:00Z'];
  const rows = [{ model: 'gfs', valid_time: times[0], precipitation: 3 }];
  const window = modelWindow(rows,'gfs','precipitation',times);
  assert.equal(window.count,1);
  assert.equal(window.sum,3);
  assert.equal(window.points[1].value,null);
});
