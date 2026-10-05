import {getForecast,models} from './forecast.mjs';
import {fitRouter,route} from './science/router.mjs';
import {detectRegime,seasonFor} from './science/regimes.mjs';
import {magnitude,mean} from './science/metrics.mjs';

export function analyzeForecast(data,now=Date.now()) {
  const ids=Object.keys(models),times=[...new Set(data.rows.map(r=>r.valid_time))].filter(t=>Date.parse(t)>now).sort();
  const fields={temperature:'temperature_2m',rainfall:'precipitation',wind_uv:'wind_speed_10m'};
  const fits=Object.fromEntries(Object.keys(fields).map(variable=>[variable,fitRouter([],{variable,models:ids,cutoff:data.retrieved_at})]));
  const series=times.map(time=>{
    const rows=data.rows.filter(r=>r.valid_time===time),out={time,hours_ahead:(Date.parse(time)-now)/3600000,lead_hours:null};
    for(const variable of Object.keys(fields)) {
      const predictions=Object.fromEntries(rows.flatMap(r=>{const value=variable==='wind_uv'?(Number.isFinite(r.u10)&&Number.isFinite(r.v10)?[r.u10,r.v10]:null):r[fields[variable]];return value===null||value===undefined?[]:[[r.model,value]];}));
      const result=route(fits[variable],{},predictions),values=Object.values(predictions).map(magnitude),spread=values.length>1?Math.max(...values)-Math.min(...values):null;
      out[variable]={...result,predictions,spread,disagreement:spread===null?'Not measurable':spread<(variable==='temperature'?2:variable==='rainfall'?2:3)?'Low':spread<(variable==='temperature'?5:variable==='rainfall'?10:7)?'Moderate':'High'};
    }
    return out;
  });
  const next24=series.slice(0,24),complete=next24.length===24&&next24.every((r,i)=>!i||Date.parse(r.time)-Date.parse(next24[i-1].time)===3600000);
  const rain24=complete&&next24.every(r=>Number.isFinite(r.rainfall.value))?next24.reduce((s,r)=>s+r.rainfall.value,0):null;
  const month=Number(new Intl.DateTimeFormat('en',{month:'numeric',timeZone:'Asia/Kolkata'}).format(new Date(now)));
  const regime=detectRegime({temperature:series[0]?.temperature.value,wind:series[0]?.wind_uv.speed,rain24,month,weather_code:data.overview?.current?.weather_code});
  const health=ids.map(id=>{
    const rows=data.rows.filter(r=>r.model===id),missing=rows.reduce((n,r)=>n+Object.values(fields).filter(f=>r[f]===null).length,0);
    return {model:id,available:rows.length>0,status:!rows.length?'Unavailable':Date.parse(data.expires_at)<=now?'Retrieval expired':missing?'Partial values':'Retrieved recently',missing_values:missing,rows:rows.length,retrieved_at:data.retrieved_at,run_initialization:null,run_age_hours:null,note:data.source_status.find(s=>s.model===id)?.message};
  });
  const stages=[['Fetch model forecasts',series.length?'complete':'failed'],['Validate data',health.every(s=>s.available&&!s.missing_values)?'complete':'partial'],['Fetch observations','not connected'],['Calculate location / season context','complete'],['Detect weather regime','complete'],['Retrieve matched historical skill','awaiting archive'],['Calculate adaptive weights','equal-source fallback'],['Generate blended forecast',series.length?'complete':'failed'],['Measure model disagreement','complete'],['Screen extreme events','deterministic screening'],['Validate against observations','awaiting observations'],['Update skill database','requires durable ingestion']];
  return {data_kind:'real',evidence_label:'REAL MODEL FORECASTS / EXPERIMENTAL BLEND',location:data.requested_location,coordinates:data.requested_coordinates,retrieved_at:data.retrieved_at,expires_at:data.expires_at,source_status:health,sources:data.sources,series,summary:{temperature:series[0]?.temperature.value??null,rain24,wind:series[0]?.wind_uv.speed??null,wind_direction:series[0]?.wind_uv.direction??null,valid_time:series[0]?.time,window_start:next24[0]?new Date(Date.parse(next24[0].time)-3600000).toISOString():null,window_end:next24.at(-1)?.time},regime,season:seasonFor(month),historical_skill:null,skill_improvement:null,observations:{status:'not connected',note:'Provider current conditions are model estimates, not verifying observations.'},context:{elevation_m:data.returned_coordinates?.elevation??null,coast_km:null,monsoon_zone:null,note:'Grid elevation from provider; coast distance and monsoon zone require a geospatial lookup.'},pipeline:stages.map(([name,status],i)=>({step:i+1,name,status})),scheduling:{mode:'request driven',refresh_minutes:15,last_execution:data.retrieved_at,next_client_refresh:data.expires_at,background_scheduler:'planned; durable hosted archive not configured'},explanation:['Weights are equal among sources with valid values at each target time.','No matched real observation archive is connected, so regional or recent skill advantages are not asserted.','Missing source values receive zero weight; remaining weights sum to 100%.','Wind blends east/north components before calculating speed and direction.','Live latest-series data has no single model-run timestamp; hours ahead is not initialization lead time.','Regime rules describe forecast conditions. They change learned weights only when matched regime-specific skill exists.']};
}
export async function getIntelligence(location){return analyzeForecast(await getForecast(location));}
