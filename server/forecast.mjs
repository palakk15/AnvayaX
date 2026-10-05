import { createHash } from 'node:crypto';
import { cached, fetchJSON, ServiceError } from './http.mjs';
export const models = Object.freeze({
  gfs_global: { name: 'NOAA GFS', agency: 'NOAA/NCEP' },
  ecmwf_ifs025: { name: 'ECMWF IFS', agency: 'ECMWF' },
  ecmwf_aifs025_single: { name: 'ECMWF AIFS Single', agency: 'ECMWF' },
  icon_global: { name: 'DWD ICON Global', agency: 'DWD' },
  gem_global: { name: 'ECCC GEM Global', agency: 'ECCC' },
  jma_gsm: { name: 'JMA GSM', agency: 'JMA' },
});
const fields = Object.freeze({ precipitation: 'mm', temperature_2m: '°C', wind_speed_10m: 'm/s', wind_direction_10m: '°' });
const endpoint = 'https://api.open-meteo.com/v1/forecast';

// Adapter contract: new providers implement these three methods, returning normalized rows.
export class OpenMeteoAdapter {
  constructor(id){if(!models[id])throw Error('Unknown model');this.id=id;}
  getMetadata(){return {id:this.id,...models[this.id],provider:'Open-Meteo',endpoint,license:'CC BY 4.0',run_initialization_available:false};}
  fetchForecast(location){return sourceForecast(location,this.id);}
  getAvailability(result){return {model:this.id,available:result.status==='fulfilled',message:result.status==='fulfilled'?'Available':result.reason?.message||'Unavailable'};}
}
export const modelAdapters=Object.keys(models).map(id=>new OpenMeteoAdapter(id));

async function sourceForecast(location, model) {
  const url = new URL(endpoint);
  Object.entries({latitude:location.latitude, longitude:location.longitude, hourly:Object.keys(fields).join(','), models:model, forecast_hours:96, timezone:'UTC', wind_speed_unit:'ms'}).forEach(([k,v])=>url.searchParams.set(k,String(v)));
  const data = await fetchJSON(url);
  const rows=normalizeProvider(data,[model]);
  if(!rows.some(row=>Date.parse(row.valid_time)>Date.now()))throw new ServiceError('This source has no future forecast values.');
  return { model, rows, source_url:url.toString(), grid:{latitude:data.latitude,longitude:data.longitude,elevation:data.elevation} };
}
async function dailyForecast(location) {
  const url = new URL(endpoint);
  Object.entries({latitude:location.latitude,longitude:location.longitude,current:'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m',daily:'temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset',forecast_days:7,timezone:'Asia/Kolkata',wind_speed_unit:'ms'}).forEach(([k,v])=>url.searchParams.set(k,String(v)));
  const data=await fetchJSON(url);
  if (!data.daily?.time?.length || !data.current) throw new ServiceError('Daily overview is unavailable.');
  const dailyUnits={temperature_2m_max:'°C',temperature_2m_min:'°C',precipitation_sum:'mm',precipitation_probability_max:'%',wind_speed_10m_max:'m/s'};
  for(const [field,unit] of Object.entries(dailyUnits)) {
    if(data.daily_units?.[field]!==unit || !Array.isArray(data.daily[field]) || data.daily[field].length!==data.daily.time.length || data.daily[field].some(v=>v!==null && !Number.isFinite(v)))throw new ServiceError('Daily overview failed its data-quality check.');
  }
  if(data.current_units?.temperature_2m!=='°C' || data.current_units?.wind_speed_10m!=='m/s' || data.utc_offset_seconds!==19800 || !/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(data.current.time))throw new ServiceError('Current overview failed its data-quality check.');
  return {current:data.current,current_units:data.current_units,daily:data.daily,daily_units:data.daily_units,source_url:url.toString(),source:'Open-Meteo automatic model selection',note:'Current conditions are model estimates, not measurements from a local station. Daily values use the provider’s automatic model selection, separate from the named source series.'};
}
export async function getForecast(location) {
  const key=`forecast:${location.latitude.toFixed(4)}:${location.longitude.toFixed(4)}`;
  const forecast=await cached(key,15*60*1000,async()=>{
    const ids=Object.keys(models);
    const results=await Promise.allSettled([...modelAdapters.map(adapter=>adapter.fetchForecast(location)),dailyForecast(location)]);
    const available=results.slice(0,ids.length).flatMap(r=>r.status==='fulfilled'?[r.value]:[]);
    if (!available.length) throw new ServiceError('The forecast sources are unavailable right now. Please retry in a few minutes.');
    const retrieved_at=new Date().toISOString();
    const rows=available.flatMap(r=>r.rows);
    const status=ids.map((id,i)=>({model:id,name:models[id].name,available:results[i].status==='fulfilled',message:results[i].status==='rejected'?results[i].reason.message:'Available'}));
    const overview=results.at(-1).status==='fulfilled'?results.at(-1).value:null;
    return {schema_version:2,data_kind:'real',evidence_kind:'provider_forecast',rows,models,units:fields,retrieved_at,expires_at:new Date(Date.now()+15*60*1000).toISOString(),source_status:status,overview,
      overview_status:overview?'available':'temporarily unavailable',returned_coordinates:available[0].grid,
      sources:available.map(({model,source_url,grid})=>({model,source_url,grid})),source_url:available[0].source_url,
      sha256:createHash('sha256').update(JSON.stringify(rows)).digest('hex'),
      model_run_initialization:null,skill_verified:false,trained_blend_applied:false,
      attribution:'Weather data: Open-Meteo (CC BY 4.0); named forecasts: NOAA/NCEP, ECMWF, DWD, ECCC and JMA.',documentation_url:'https://open-meteo.com/en/docs',license_url:'https://open-meteo.com/en/terms',
      notes:['Values are model forecasts, not station observations.','Hourly data may be interpolated from coarser native model steps.','Precipitation is the preceding-hour total, including snow water equivalent.','Model disagreement is not a calibrated confidence interval.','Latest-series retrieval does not identify a single model initialization cycle.','Research weights are not applied to these live forecasts.']};
  });
  return {...forecast,city:location.name,requested_location:location,requested_coordinates:{latitude:location.latitude,longitude:location.longitude}};
}

export function normalizeProvider(data, selectedModels = Object.keys(models)) {
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
  for (const model of selectedModels) {
    for (const [field, unit] of Object.entries(fields)) {
      const key = Object.hasOwn(h, `${field}_${model}`) ? `${field}_${model}` : field;
      if (!Array.isArray(h[key]) || h[key].length !== times.length || data.hourly_units?.[key] !== unit) {
        throw new Error(`Missing field or unit: ${key}`);
      }
    }
    for (let i = 0; i < times.length; i++) {
      const row = { model, valid_time: `${times[i]}:00Z`, precipitation_start: new Date(Date.parse(`${times[i]}:00Z`) - 3600000).toISOString() };
      for (const field of Object.keys(fields)) {
        const key = Object.hasOwn(h, `${field}_${model}`) ? `${field}_${model}` : field;
        const value = h[key][i];
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
