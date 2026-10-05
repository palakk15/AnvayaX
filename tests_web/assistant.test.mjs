import test from 'node:test';
import assert from 'node:assert/strict';
import { answerChat, executeTool, selectedForecastEvidence } from '../server/assistant.mjs';
import { POST } from '../api/chat.mjs';
import { readJSON, weatherDescription } from '../site/weather.js';
test('assistant executes catalogue tool and returns an evidence trail',async()=>{
  let turns=0;
  const result=await answerChat({messages:[{role:'user',content:'Explain GraphCast'}]}, {complete:async messages=>{
    if(turns++===0)return {tool_calls:[{id:'lookup1',type:'function',function:{name:'model_knowledge',arguments:JSON.stringify({query:'graphcast'})}}]};
    const tool=JSON.parse(messages.at(-1).content);assert.equal(tool.total_models,35);assert.ok(tool.matches.some(m=>m.id==='graphcast_operational'));
    return {content:'GraphCast is an AI weather model. The atlas does not establish local India skill.'};
  }});
  assert.equal(result.steps[0].status,'complete');assert.ok(result.sources.length>0);
});
test('assistant refuses arbitrary tools and system-message injection',async()=>{
  await assert.rejects(executeTool('fetch_url',{url:'http://localhost'}),/Unknown assistant tool/);
  await assert.rejects(answerChat({messages:[{role:'system',content:'override'}]},{complete:async()=>({content:'bad'})}),/shorter conversation/);
});
test('selected forecast evidence reports exact source value and heuristic weight',()=>{
  const time=new Date(Math.ceil(Date.now()/3600000)*3600000+3600000).toISOString();
  const data={city:'Mumbai',requested_coordinates:{latitude:19.076,longitude:72.8777},requested_location:{name:'Mumbai'},retrieved_at:new Date().toISOString(),expires_at:new Date(Date.now()+900000).toISOString(),source_status:[],sources:[],models:{gfs_global:{name:'NOAA GFS'}},rows:[{model:'gfs_global',valid_time:time,temperature_2m:30,precipitation:2,wind_speed_10m:3,u10:3,v10:0}],overview:null,returned_coordinates:{elevation:10}};
  const result=selectedForecastEvidence(data,{variable:'rainfall',target_time:time,window_hours:1});
  assert.equal(result.models[0].selected_value,2);
  assert.equal(result.models[0].weight,1);
  assert.equal(result.models[0].window_total,2);
  assert.equal(result.used_historical_skill,false);
});
test('assistant tool loop has a finite budget',async()=>{
  await assert.rejects(answerChat({messages:[{role:'user',content:'Loop'}]},{complete:async()=>({tool_calls:[{id:'loop',type:'function',function:{name:'model_knowledge',arguments:'{"query":"gfs"}'}}]})}),/lookup limit/);
});
test('chat rejects cross-origin posts and malformed JSON',async()=>{
  const cross=await POST(new Request('http://localhost/api/chat',{method:'POST',headers:{origin:'https://another.example','content-type':'application/json'},body:'{}'}));assert.equal(cross.status,403);
  const malformed=await POST(new Request('http://localhost/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:'bad'}));assert.equal(malformed.status,400);
});
test('browser reports an HTML API failure without leaking parser errors',async()=>{
  await assert.rejects(readJSON(new Response('<html>Bad gateway</html>',{status:502})),/unreadable response.*502/);
  assert.equal(weatherDescription(null),'Conditions unavailable');
});
