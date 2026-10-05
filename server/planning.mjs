import {cached,fetchJSON} from './http.mjs';
import {quantile} from './science/metrics.mjs';
const defaults=[10,35,8],limits=[[10,50],[30,42],[8,14]],fields=['precipitation_sum','temperature_2m_max','wind_speed_10m_max'],units=['mm','°C','m/s'];
export function planningContext(data,start,end){
  const days=data?.daily?.time||[];
  const thresholds=fields.map((field,i)=>{
    const aligned=days.length===30&&days.every((d,j)=>/^\d{4}-\d\d-\d\d$/.test(d)&&(!j||Date.parse(d)-Date.parse(days[j-1])===86400000))&&days[0]===start&&days.at(-1)===end&&data.daily?.[field]?.length===days.length&&data.daily_units?.[field]===units[i];
    const values=aligned?data.daily[field].filter(v=>Number.isFinite(v)&&(i===1||v>=0)):[];
    const sample=i===0?values.filter(v=>v>=1):values;
    const minimum=i===0?5:20,usable=values.length>=20&&sample.length>=minimum;
    const percentile=i===0?.75:.9,raw=usable?quantile(sample,percentile):null;
    const value=raw===null?defaults[i]:Math.round(Math.min(limits[i][1],Math.max(limits[i][0],raw))*10)/10;
    return {value,unit:units[i],days:values.length,sample_days:sample.length,raw_percentile:raw,percentile,limits:limits[i],basis:usable?'recent reanalysis':'fallback planning cutoff',description:usable?`${i===0?'75th percentile of wet-day totals':'90th percentile of daily maxima'} in the recent reference period, bounded to ${limits[i][0]}–${limits[i][1]} ${units[i]}.`:'Insufficient recent reference coverage; a fixed exploratory planning cutoff is used.'};
  });
  return {start,end,expected_days:30,source:'ERA5 reanalysis · modeled recent weather, not station observations or long-term climate normals',thresholds,note:'Local planning cutoffs are experimental, recomputed daily and not official warning criteria. They do not measure vulnerability or prove improved forecast skill.',documentation_url:'https://open-meteo.com/en/docs/historical-weather-api'};
}
export async function getPlanningContext(location,now=Date.now()){
  const end=new Date(now-7*86400000).toISOString().slice(0,10),start=new Date(Date.parse(end)-29*86400000).toISOString().slice(0,10);
  return cached(`planning:${location.latitude.toFixed(4)}:${location.longitude.toFixed(4)}:${end}`,86400000,async()=>{
    const url=new URL('https://archive-api.open-meteo.com/v1/archive');
    Object.entries({latitude:location.latitude,longitude:location.longitude,start_date:start,end_date:end,daily:fields.join(','),models:'era5',timezone:'Asia/Kolkata',wind_speed_unit:'ms'}).forEach(([k,v])=>url.searchParams.set(k,v));
    try{return {...planningContext(await fetchJSON(url,{timeout:10000}),start,end),source_url:url.toString()};}
    catch{return planningContext(null,start,end);}
  });
}
