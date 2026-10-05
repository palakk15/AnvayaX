import { createHash } from 'node:crypto';

const endpoint = 'https://api.open-meteo.com/v1/forecast';
export const cities = Object.freeze({
  Delhi: [28.6139, 77.2090], Mumbai: [19.0760, 72.8777],
  Bengaluru: [12.9716, 77.5946], Chennai: [13.0827, 80.2707],
  Kolkata: [22.5726, 88.3639], Hyderabad: [17.3850, 78.4867],
  Guwahati: [26.1445, 91.7362], Jaipur: [26.9124, 75.7873],
});
export const models = Object.freeze({
  gfs_global: { name: 'NOAA GFS', agency: 'NOAA/NCEP' },
  ecmwf_ifs025: { name: 'ECMWF IFS', agency: 'ECMWF' },
  ecmwf_aifs025_single: { name: 'ECMWF AIFS Single', agency: 'ECMWF' },
});
const fields = Object.freeze({ precipitation: 'mm', temperature_2m: '°C', wind_speed_10m: 'm/s', wind_direction_10m: '°' });
const byteLimit = 2_000_000;

export function normalizeProvider(data) {
  const h = data?.hourly;
  const times = h?.time;
  if (!Array.isArray(times) || times.length < 1 || times.length > 96 || data.utc_offset_seconds !== 0) {
    throw new Error('Invalid UTC hourly forecast series');
  }
  let previous = null;
  for (const time of times) {
    if (typeof time !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(time)) throw new Error('Invalid forecast timestamp');
    const milliseconds = Date.parse(`${time}:00Z`);
    if (!Number.isFinite(milliseconds) || (previous !== null && milliseconds - previous !== 3600000)) throw new Error('Noncontiguous forecast timestamps');
    previous = milliseconds;
  }
  const rows = [];
  for (const model of Object.keys(models)) {
    for (const [field, unit] of Object.entries(fields)) {
      const key = `${field}_${model}`;
      if (!Array.isArray(h[key]) || h[key].length !== times.length || data.hourly_units?.[key] !== unit) {
        throw new Error(`Missing field or unit: ${key}`);
      }
    }
    for (let i = 0; i < times.length; i++) {
      const row = { model, valid_time: `${times[i]}:00Z`, precipitation_start: new Date(Date.parse(`${times[i]}:00Z`) - 3600000).toISOString() };
      for (const field of Object.keys(fields)) {
        const value = h[`${field}_${model}`][i];
        if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) throw new Error('Invalid forecast value');
        if (value !== null && ((['precipitation', 'wind_speed_10m'].includes(field) && value < 0) || (field === 'wind_direction_10m' && (value < 0 || value > 360)))) throw new Error('Out-of-range forecast value');
        row[field] = value;
      }
      const speed = row.wind_speed_10m;
      const bearing = row.wind_direction_10m;
      row.u10 = speed === null || bearing === null ? null : -speed * Math.sin(bearing * Math.PI / 180);
      row.v10 = speed === null || bearing === null ? null : -speed * Math.cos(bearing * Math.PI / 180);
      rows.push(row);
    }
  }
  if (!rows.some(row => row.temperature_2m !== null)) throw new Error('No usable temperature forecasts');
  return rows;
}

export async function GET(request) {
  const city = new URL(request.url).searchParams.get('city') || 'Delhi';
  if (!Object.hasOwn(cities, city)) return Response.json({ error: 'Choose a supported city.' }, { status: 400 });
  const [latitude, longitude] = cities[city];
  const url = new URL(endpoint);
  Object.entries({ latitude, longitude, hourly: Object.keys(fields).join(','), models: Object.keys(models).join(','), forecast_days: 3, timezone: 'UTC', wind_speed_unit: 'ms' })
    .forEach(([key, value]) => url.searchParams.set(key, String(value)));
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(8500), redirect: 'manual', headers: { Accept: 'application/json' } });
    if (response.status !== 200 || Number(response.headers.get('content-length') || 0) > byteLimit) throw new Error('Provider unavailable or response too large');
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > byteLimit) throw new Error('Provider response too large');
    const raw = Buffer.from(buffer);
    const provider = JSON.parse(raw.toString('utf8'));
    const rows = normalizeProvider(provider);
    return Response.json({
      schema_version: 1, data_kind: 'real', evidence_kind: 'provider_forecast', city,
      requested_coordinates: { latitude, longitude },
      returned_coordinates: { latitude: provider.latitude, longitude: provider.longitude, elevation: provider.elevation },
      retrieved_at: new Date().toISOString(), source_url: url.toString(), sha256: createHash('sha256').update(raw).digest('hex'), bytes: raw.length,
      models, units: fields, rows, model_run_initialization: null, model_checkpoint_version: null,
      historical_publication_time_verified: false, skill_verified: false, trained_blend_applied: false,
      attribution: 'Weather data by Open-Meteo.com; upstream forecasts by NOAA/NCEP and ECMWF.',
      documentation_url: 'https://open-meteo.com/en/docs', license_url: 'https://open-meteo.com/en/terms',
      notes: [
        'Named provider model forecasts are not station observations.',
        'This latest-series endpoint does not pin a single model initialization cycle or checkpoint.',
        'Hourly values can involve provider interpolation and elevation adjustment.',
        'Precipitation is the preceding-hour total, including rain, showers and snow water equivalent.',
        'A range across models is not a calibrated probability interval.',
        'The synthetic demonstration gate has not been applied to these real forecasts.'
      ],
    }, { headers: { 'Cache-Control': 'public, s-maxage=900, stale-while-revalidate=300' } });
  } catch (error) {
    return Response.json({ error: 'Live provider data is temporarily unavailable.', detail: String(error.message || error) }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
  }
}

export default GET;
