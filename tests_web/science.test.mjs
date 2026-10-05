import test from 'node:test';
import assert from 'node:assert/strict';
import {deterministicMetrics,eventMetrics,normalizeWeights,blendValues,ensembleProbability} from '../server/science/metrics.mjs';
import {fitRouter,route} from '../server/science/router.mjs';
import {walkForward,summarizeValidation} from '../server/science/validation.mjs';
import {ensembleGuidance} from '../server/ensemble.mjs';
import {analyzeForecast} from '../server/intelligence.mjs';
import {GET} from '../api/intelligence.mjs';

test('metrics match analytical errors; undefined correlations and event denominators remain null',()=>{
  const m=deterministicMetrics([{forecast:2,observed:1},{forecast:4,observed:2},{forecast:null,observed:5}]);
  assert.equal(m.n,2);assert.equal(m.mae,1.5);assert.equal(m.bias,1.5);assert.equal(m.rmse,Math.sqrt(2.5));assert.equal(m.correlation,1);
  assert.equal(deterministicMetrics([{forecast:1,observed:1}]).correlation,null);
  const e=eventMetrics([{forecast:2,observed:2,probability:.8},{forecast:2,observed:0,probability:.6},{forecast:0,observed:2,probability:.3},{forecast:0,observed:0,probability:.1}],1);
  assert.equal(e.csi,1/3);assert.equal(e.pod,.5);assert.equal(e.far,.5);assert.equal(e.f1,.5);assert.ok(Math.abs(e.brier-.225)<1e-10);assert.equal(e.reliability.reduce((n,b)=>n+b.n,0),4);
  assert.equal(eventMetrics([{forecast:0,observed:0}],1).brier,null);
});
test('failover renormalizes valid sources and wind crosses north through vector arithmetic',()=>{
  assert.deepEqual(normalizeWeights({a:1,b:Infinity,c:-2}),{a:1,b:0,c:0});
  assert.deepEqual(blendValues({a:20,b:30},{a:.25,b:.5,c:.25}).weights,{a:1/3,b:2/3,c:0});
  const uv=deg=>[-10*Math.sin(deg*Math.PI/180),-10*Math.cos(deg*Math.PI/180)];
  const b=blendValues({a:uv(350),b:uv(10)},{a:.5,b:.5});assert.ok(b.direction<1e-8);assert.ok(b.speed>9.8);
  assert.equal(blendValues({a:[1,0],b:[-1,0]},{a:.5,b:.5}).direction,null);
  assert.equal(blendValues({a:null},{a:1}).value,null);assert.equal(ensembleProbability([10],5),null);
});
function cases(){return Array.from({length:40},(_,i)=>{const date=new Date(Date.UTC(2025,0,1+i*2)),issued=date.toISOString(),valid=new Date(+date+86400000).toISOString();return {id:String(i),variable:'temperature',data_kind:'synthetic',issued_at:issued,valid_start:valid,valid_end:valid,observed_at:new Date(+date+90000000).toISOString(),observed:20+i%4,predictions:{a:21+i%4,b:24+i%4},members:{a:[21+i%4],b:[24+i%4]},state:'A',district:'D',region:'R',season:'winter',month:1,regime:'normal',lead_hours:24,location_id:'test',latitude:20,longitude:74};});}
test('router excludes late observations and future labels even when issued in the past',()=>{
  const rows=cases(),cutoff=rows[20].issued_at,fit=fitRouter(rows,{variable:'temperature',models:['a','b'],cutoff});
  const corrupted=rows.map(c=>Date.parse(c.observed_at)>=Date.parse(cutoff)?{...c,observed:-10000}:c);
  assert.deepEqual(fitRouter(corrupted,{variable:'temperature',models:['a','b'],cutoff}),fit);
  const delayed=rows.map(c=>({...c,observed_at:rows.at(-1).observed_at}));assert.equal(fitRouter(delayed,{variable:'temperature',models:['a','b'],cutoff}).n,0);
  const r=route(fit,rows[20],rows[20].predictions);assert.ok(r.weights.a>r.weights.b);assert.ok(Math.abs(Object.values(r.weights).reduce((a,b)=>a+b,0)-1)<1e-10);
});
test('walk-forward decisions do not change when unseen test observations are changed',()=>{
  const rows=cases(),run=walkForward(rows),firstFold=run.folds[0],mutated=rows.map(c=>c.issued_at>=firstFold.testing_start?{...c,observed:999}:c),changed=walkForward(mutated);
  const end=firstFold.testing_end;
  assert.deepEqual(changed.rows.filter(c=>c.issued_at<end).map(c=>c.weights),run.rows.filter(c=>c.issued_at<end).map(c=>c.weights));
  for(const r of run.rows)assert.ok(Date.parse(r.skill_cutoff)<=Date.parse(r.issued_at));
  assert.equal(summarizeValidation(run,{variable:'temperature'}).improvement_percent,null);
  assert.equal(summarizeValidation(run,{variable:'temperature',state:'Missing'}).n,0);
});
test('common source support excludes missing-model cases from comparative skill',()=>{
  const rows=cases();delete rows[1].predictions.b;
  const fit=fitRouter(rows,{variable:'temperature',models:['a','b'],cutoff:'2026-01-01T00:00:00Z'});
  assert.equal(fit.global.a.n,39);assert.equal(fit.global.b.n,39);
});
test('walk-forward groups equivalent issuance instants across timezone offsets',()=>{
  const rows=cases(),canonical=walkForward(rows),offset=rows.map(c=>({...c,issued_at:c.issued_at.replace('T00:00:00.000Z','T05:30:00+05:30')}));
  assert.deepEqual(walkForward(offset).rows.map(c=>c.weights),canonical.rows.map(c=>c.weights));
});
test('ensemble guidance requires complete target windows, units and member support',()=>{
  const now=Date.parse('2025-01-01T00:00:00Z'),time=Array.from({length:25},(_,i)=>new Date(now+i*3600000).toISOString().slice(0,16)),hourly={time},hourly_units={};
  for(const [f,u,v] of [['temperature_2m','°C',41],['precipitation','mm',3],['wind_speed_10m','m/s',10]])for(let m=0;m<31;m++){const key=f+(m?`_member${String(m).padStart(2,'0')}`:'');hourly[key]=time.map(()=>v);hourly_units[key]=u;}
  const data={hourly,hourly_units,utc_offset_seconds:0},r=ensembleGuidance(data,now);
  assert.equal(r.events[0].probability,1);assert.equal(r.events[0].p50,72);assert.equal(r.events[2].probability,0);
  hourly.precipitation_member01[3]=null;assert.equal(ensembleGuidance(data,now).events[0].member_count,30);
  hourly_units.temperature_2m='K';assert.equal(ensembleGuidance(data,now).events[1].member_count,30);
  hourly.time.splice(1,1);assert.throws(()=>ensembleGuidance(data,now),/time axis/);
});
test('live analysis renormalizes failures and never promotes current estimates to observations',()=>{
  const now=Date.parse('2025-01-01T00:00:00Z'),rows=Array.from({length:24},(_,i)=>({model:'gfs_global',valid_time:new Date(now+(i+1)*3600000).toISOString(),temperature_2m:30,precipitation:2,wind_speed_10m:5,u10:0,v10:-5}));
  const r=analyzeForecast({rows,retrieved_at:new Date(now).toISOString(),expires_at:new Date(now+900000).toISOString(),requested_location:{name:'Test'},source_status:[],sources:[]},now);
  assert.equal(r.summary.rain24,48);assert.equal(r.series[0].temperature.weights.gfs_global,1);assert.equal(r.series[0].temperature.weights.ecmwf_ifs025,0);assert.equal(r.series[0].temperature.spread,null);assert.equal(r.skill_improvement,null);assert.equal(r.observations.status,'not connected');assert.equal(r.series[0].lead_hours,null);
});
test('real archive stays empty and invalid evidence queries return readable 400 JSON',async()=>{
  const response=await GET(new Request('http://localhost/api/intelligence?action=skill'));assert.equal(response.status,200);const d=await response.json();assert.equal(d.n,0);assert.equal(d.improvement_percent,null);
  const bad=await GET(new Request('http://localhost/api/intelligence?action=skill&mode=fake'));assert.equal(bad.status,400);assert.equal(typeof (await bad.json()).error,'string');
});
