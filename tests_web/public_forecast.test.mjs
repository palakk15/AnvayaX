import test from 'node:test';
import assert from 'node:assert/strict';
import { GET, normalizeProvider, models } from '../api/public-forecast.mjs';

function fixture() {
  const hourly = { time: ['2026-09-27T00:00', '2026-09-27T01:00'] };
  const hourly_units = {};
  const units = { precipitation: 'mm', temperature_2m: '°C', wind_speed_10m: 'm/s', wind_direction_10m: '°' };
  for (const model of Object.keys(models)) {
    for (const [field, unit] of Object.entries(units)) {
      const key = `${field}_${model}`;
      hourly_units[key] = unit;
      hourly[key] = field === 'temperature_2m' ? [30, 31] : field === 'wind_direction_10m' ? [270, 90] : field === 'wind_speed_10m' ? [4, 6] : [0, 2];
    }
  }
  return { utc_offset_seconds: 0, latitude: 19.06, longitude: 72.87, elevation: 5, hourly, hourly_units };
}

test('normalizes every named model and preserves physical vector direction', () => {
  const rows = normalizeProvider(fixture());
  assert.equal(rows.length, 6);
  assert.equal(rows[0].precipitation_start, '2026-09-26T23:00:00.000Z');
  assert.ok(Math.abs(rows[0].u10 - 4) < 1e-9);
  assert.ok(Math.abs(rows[1].u10 + 6) < 1e-9);
});

test('fails closed on missing units, negative precipitation and noncontiguous time', () => {
  const missing = fixture(); delete missing.hourly_units.precipitation_gfs_global;
  assert.throws(() => normalizeProvider(missing), /Missing field or unit/);
  const negative = fixture(); negative.hourly.precipitation_gfs_global[0] = -1;
  assert.throws(() => normalizeProvider(negative), /Out-of-range/);
  const gaps = fixture(); gaps.hourly.time[1] = '2026-09-27T03:00';
  assert.throws(() => normalizeProvider(gaps), /Noncontiguous/);
});

test('rejects an unsupported city before contacting the provider', async () => {
  const response = await GET(new Request('http://localhost/api/public-forecast?city=Unknown'));
  assert.equal(response.status, 400);
});

test('serves attributable real provider data but never declares a trained blend', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(fixture()), { status: 200 });
  try {
    const response = await GET(new Request('http://localhost/api/public-forecast?city=Mumbai'));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.data_kind, 'real');
    assert.equal(body.evidence_kind, 'provider_forecast');
    assert.equal(body.trained_blend_applied, false);
    assert.equal(body.skill_verified, false);
    assert.equal(body.rows.length, 6);
    assert.match(body.sha256, /^[0-9a-f]{64}$/);
  } finally { globalThis.fetch = original; }
});
