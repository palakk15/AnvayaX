export const weatherLabels = {precipitation:'Hourly rain & snow equivalent',temperature_2m:'Air temperature',wind_speed_10m:'Wind speed'};
export const weatherHelp = {precipitation:'Water falling during the preceding hour, including snow water equivalent. A value of 1 mm means one litre per square metre.',temperature_2m:'Air temperature two metres above ground: the standard weather measurement, rather than ground-surface temperature.',wind_speed_10m:'Wind speed ten metres above ground, the standard reference height. 1 m/s = 3.6 km/h.'};
export async function readJSON(response) {
  const text=await response.text();let data;
  try {data=JSON.parse(text);}catch{throw Error(`The weather service returned an unreadable response (HTTP ${response.status}). Please retry.`);}
  if(!response.ok)throw Error(typeof data.error==='string'?data.error:`Request failed (HTTP ${response.status}). Please retry.`);
  return data;
}
export function weatherDescription(code) {
  if(!Number.isFinite(code))return 'Conditions unavailable';
  if(code===0)return 'Clear sky';if(code<=3)return 'Partly cloudy';if(code<=48)return 'Fog';if(code<=57)return 'Drizzle';if(code<=67)return 'Rain';if(code<=77)return 'Snow';if(code<=82)return 'Rain showers';if(code<=86)return 'Snow showers';if(code>=95)return 'Thunderstorms';return 'Conditions unavailable';
}
export function downloadForecast(data) {
  const header=['model','valid_time_utc','temperature_c','precipitation_mm','wind_ms','retrieved_at_utc'];
  const rows=data.rows.map(r=>[r.model,r.valid_time,r.temperature_2m,r.precipitation,r.wind_speed_10m,data.retrieved_at].map(v=>v??'').join(','));
  const url=URL.createObjectURL(new Blob([[header.join(','),...rows].join('\n')],{type:'text/csv'}));
  const a=document.createElement('a');a.href=url;a.download='anvaya-source-forecasts.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
