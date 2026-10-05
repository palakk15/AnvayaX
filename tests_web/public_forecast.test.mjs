import test from 'node:test';
import assert from 'node:assert/strict';
import { GET, normalizeProvider, models } from '../api/public-forecast.mjs';
import { GET as searchLocations, normalizeLocations } from '../api/locations.mjs';

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
  globalThis.fetch = async url => {
    assert.equal(new URL(url).searchParams.get('forecast_hours'), '96');
    return new Response(JSON.stringify(fixture()), { status: 200 });
  };
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

test('searches Indian places and excludes results outside India', async () => {
  assert.deepEqual(normalizeLocations({ results: [
    { id: 1, name: 'Pune', admin1: 'Maharashtra', country_code: 'IN', latitude: 18.52, longitude: 73.85 },
    { id: 2, name: 'Pune', country_code: 'US', latitude: 40, longitude: -80 },
  ] }).map(place => place.name), ['Pune']);
  const original = globalThis.fetch;
  globalThis.fetch = async url => {
    assert.equal(new URL(url).searchParams.get('countryCode'), 'IN');
    return new Response(JSON.stringify({ results: [
      { id: 1275339, name: 'Mumbai', admin1: 'Maharashtra', country_code: 'IN', latitude: 19.07, longitude: 72.87 },
    ] }), { status: 200 });
  };
  try {
    const response = await searchLocations(new Request('http://localhost/api/locations?q=Mum'));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).locations[0].id, 1275339);
  } finally { globalThis.fetch = original; }
});

test('resolves an Indian place ID before fetching source forecasts', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(String(url));
    if (calls.length === 1) return new Response(JSON.stringify({
      id: 1275339, name: 'Mumbai', admin1: 'Maharashtra', country_code: 'IN', latitude: 19.07, longitude: 72.87,
    }), { status: 200 });
    return new Response(JSON.stringify(fixture()), { status: 200 });
  };
  try {
    const response = await GET(new Request('http://localhost/api/public-forecast?place_id=1275339'));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.requested_location.id, 1275339);
    assert.equal(body.city, 'Mumbai');
    assert.equal(new URL(calls[1]).searchParams.get('forecast_hours'), '96');
  } finally { globalThis.fetch = original; }
});
