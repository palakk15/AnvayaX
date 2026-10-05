// Opt-in smoke check against real public providers; never runs in the unit-test suite.
import { GET as forecast } from '../api/public-forecast.mjs';
import { GET as locations } from '../api/locations.mjs';
const queries=['411001','226001','Gangtok','Kavaratti','Port Blair'];
for(const q of queries){
  const response=await locations(new Request(`http://localhost/api/locations?q=${encodeURIComponent(q)}`));const data=await response.json();
  console.log(JSON.stringify({query:q,status:response.status,places:data.locations?.slice(0,3).map(p=>({name:p.name,state:p.state,precision:p.precision})),error:data.error}));
}
for(const params of ['city=Mumbai','latitude=27.33&longitude=88.61','latitude=10.56&longitude=72.64','latitude=11.62&longitude=92.72']){
  const response=await forecast(new Request(`http://localhost/api/public-forecast?${params}`));const data=await response.json();
  console.log(JSON.stringify({params,status:response.status,rows:data.rows?.length,models:data.source_status,days:data.overview?.daily?.time?.length,retrieved:data.retrieved_at,error:data.error}));
  if(response.status!==200)process.exitCode=1;
}
