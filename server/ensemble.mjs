import {cached,fetchJSON,ServiceError} from './http.mjs';
import {quantile,mean} from './science/metrics.mjs';
export function ensembleGuidance(data,now=Date.now()) {
  const h=data.hourly,times=h?.time;
  if(!Array.isArray(times)||data.utc_offset_seconds!==0||times.some((t,i)=>!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(t)||(i&&Date.parse(t+'Z')-Date.parse(times[i-1]+'Z')!==3600000)))throw new ServiceError('Invalid ensemble time axis');
  const indices=times.map((t,i)=>Date.parse(t+'Z')>now?i:-1).filter(i=>i>=0).slice(0,24);
  if(indices.length!==24)throw new ServiceError('A complete 24-hour ensemble window is unavailable');
  const specs=[['Heavy rainfall','precipitation','mm',64.5,'sum'],['High temperature','temperature_2m','°C',40,'max'],['High wind','wind_speed_10m','m/s',17.2,'max']];
  const events=specs.map(([name,field,unit,threshold,aggregation])=>{
    const keys=Object.keys(h).filter(k=>k===field||new RegExp(`^${field}_member\\d+$`).test(k));
    const members=keys.flatMap(k=>{
      const values=indices.map(i=>h[k]?.[i]);
      if(data.hourly_units?.[k]!==unit||h[k]?.length!==times.length||!values.every(v=>Number.isFinite(v)&&(field==='temperature_2m'||v>=0)))return [];
      return [{member:k,value:aggregation==='sum'?values.reduce((a,b)=>a+b,0):Math.max(...values)}];
    });
    const values=members.map(m=>m.value),probability=values.length>=10?values.filter(v=>v>threshold).length/values.length:null;
    return {name,threshold,unit,aggregation,member_count:members.length,expected_members:keys.length,probability,probability_kind:'Raw ensemble exceedance frequency; not locally calibrated',confidence:'Calibration unknown',severity:probability===null?'Unavailable':probability>=.5?'Elevated ensemble signal':probability>0?'Some members exceed threshold':'No member exceeds threshold; not an all-clear',p10:quantile(values,.1),p50:quantile(values,.5),p90:quantile(values,.9),ensemble_mean:mean(values),members,contributions:[{model:'NOAA GEFS',weight:1}],reliability:null};
  });
  return {events,window_start:new Date(Date.parse(times[indices[0]]+'Z')-3600000).toISOString(),window_end:times[indices.at(-1)]+'Z',note:'Thresholds are prototype screening choices. Heatwave declarations require local normals and persistence; cyclone guidance requires a separate official track/alert source. P10–P90 is ensemble dispersion, not a calibrated prediction interval.'};
}
export async function getEnsemble(location) {
  return cached(`ensemble:${location.latitude.toFixed(4)}:${location.longitude.toFixed(4)}`,30*60000,async()=>{
    const url=new URL('https://ensemble-api.open-meteo.com/v1/ensemble');
    Object.entries({latitude:location.latitude,longitude:location.longitude,hourly:'temperature_2m,precipitation,wind_speed_10m',models:'gfs_seamless',forecast_hours:48,wind_speed_unit:'ms',timezone:'UTC'}).forEach(([k,v])=>url.searchParams.set(k,v));
    return {data_kind:'real',location,provider:'NOAA GEFS via Open-Meteo',retrieved_at:new Date().toISOString(),source_url:url.toString(),documentation_url:'https://open-meteo.com/en/docs/ensemble-api',...ensembleGuidance(await fetchJSON(url))};
  });
}
