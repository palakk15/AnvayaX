import {deterministicMetrics,eventMetrics,magnitude,ensembleProbability,mean} from './metrics.mjs';
import {fitRouter,route} from './router.mjs';

export const thresholds={temperature:40,rainfall:64.5,wind_uv:17.2};
export function matches(c,filters={}) {return ['state','district','region','season','month','lead_hours','regime','location_id','variable'].every(k=>!filters[k]||filters[k]==='all'||String(c[k])===String(filters[k]));}
function evaluate(c,fitted) {
  const result=route(fitted,c,c.predictions);
  return {...c,blend:result.value,weights:result.weights,explanations:result.explanations,basis:result.basis,skill_cutoff:fitted.cutoff};
}
function scores(cases,models,threshold) {
  return [...models,'blend'].map(model=>{
    const pairs=cases.map(c=>({forecast:magnitude(model==='blend'?c.blend:c.predictions[model]),observed:magnitude(c.observed),probability:model==='blend'?null:ensembleProbability(c.members?.[model],threshold)}));
    return {model,...deterministicMetrics(pairs),...eventMetrics(pairs,threshold)};
  });
}
/** Frozen folds: labels must arrive before fitting; validation selects strength and reference. */
export function walkForward(cases,{variable='temperature',recentDays=30,foldCount=3}={}) {
  const rows=cases.filter(c=>c.variable===variable).map(c=>({...c,issued_at:new Date(c.issued_at).toISOString()})).sort((a,b)=>Date.parse(a.issued_at)-Date.parse(b.issued_at));
  const models=[...new Set(rows.flatMap(c=>Object.keys(c.predictions)))].sort();
  const common=rows.filter(c=>models.every(m=>c.predictions[m]!==undefined));
  const issues=[...new Set(common.map(c=>c.issued_at))];
  if(issues.length<10)return {models,rows:[],folds:[],reason:'At least ten distinct forecast issuance times with a common source cohort are needed.'};
  const trainEnd=issues[Math.floor(issues.length*.35)],testStart=issues[Math.floor(issues.length*.5)];
  const validation=common.filter(c=>c.issued_at>=trainEnd&&c.issued_at<testStart&&Date.parse(c.observed_at)<Date.parse(testStart)&&Date.parse(c.valid_end)<Date.parse(testStart));
  if(!validation.length)return {models,rows:[],folds:[],reason:'No validation observations arrived before the test boundary.'};
  const candidates=[0,.5,1].map(strength=>{
    const fit=fitRouter(common,{variable,models,cutoff:trainEnd,recentDays,strength});
    const error=mean(validation.map(c=>{const r=route(fit,c,c.predictions);return (magnitude(r.value)-magnitude(c.observed))**2;}));
    return {strength,rmse:Math.sqrt(error)};
  }).sort((a,b)=>a.rmse-b.rmse||a.strength-b.strength);
  const reference=scores(validation,models,thresholds[variable]).filter(s=>s.model!=='blend').sort((a,b)=>a.rmse-b.rmse)[0]?.model;
  const testIssues=issues.filter(t=>t>=testStart),folds=[],output=[];
  for(let i=0;i<foldCount;i++) {
    const start=testIssues[Math.floor(i*testIssues.length/foldCount)],end=testIssues[Math.floor((i+1)*testIssues.length/foldCount)]||null;
    if(!start)continue;
    const fit=fitRouter(common,{variable,models,cutoff:start,recentDays,strength:candidates[0].strength});
    const test=common.filter(c=>c.issued_at>=start&&(!end||c.issued_at<end));
    output.push(...test.map(c=>evaluate(c,fit)));
    folds.push({fold:i+1,training_start:issues[0],training_observations_before:start,validation_start:trainEnd,validation_end:testStart,testing_start:start,testing_end:end||test.at(-1)?.issued_at,training_n:fit.n,test_n:test.length,strength:fit.strength});
  }
  return {models,rows:output,folds,reference,tuning:candidates,initial_training_end:trainEnd,test_start:testStart,common_cases:common.length,excluded_cases:rows.length-common.length};
}
export function summarizeValidation(run,filters={},recentDays=30) {
  let rows=run.rows.filter(c=>matches(c,filters));
  const threshold=thresholds[filters.variable]??40;
  const metrics=scores(rows,run.models,threshold),reference=metrics.find(s=>s.model===run.reference),blend=metrics.find(s=>s.model==='blend');
  const real=rows.length>0&&rows.every(c=>c.data_kind==='real');
  const end=Math.max(...run.rows.map(c=>Date.parse(c.valid_end)));
  const recent=rows.filter(c=>Date.parse(c.valid_end)>=end-recentDays*86400000);
  const sites=[...new Set(rows.map(c=>c.location_id))].map(id=>{const local=rows.filter(c=>c.location_id===id),c=local.at(-1);return {...Object.fromEntries(['location_id','state','district','region','latitude','longitude','elevation_m','coast_km'].map(k=>[k,c[k]])),n:local.length,weights:Object.fromEntries(run.models.map(m=>[m,mean(local.map(r=>r.weights[m]))])),metrics:scores(local,run.models,threshold),state_metrics:scores(rows.filter(r=>r.state===c.state),run.models,threshold)};});
  return {n:rows.length,models:run.models,metrics,threshold,threshold_units:filters.variable==='rainfall'?'mm / 24 h':filters.variable==='wind_uv'?'m/s':'degrees C',reference:run.reference,improvement_percent:real&&reference?.rmse>0?100*(reference.rmse-blend.rmse)/reference.rmse:null,improvement_note:real?'Test RMSE relative to the single source selected on validation; descriptive, not statistical significance.':'Operational skill improvement is withheld for synthetic or absent observations.',recent:{days:recentDays,as_of:Number.isFinite(end)?new Date(end).toISOString():null,metrics:scores(recent,run.models,threshold)},sites,folds:run.folds,tuning:run.tuning,common_cases:run.common_cases,excluded_cases:run.excluded_cases,reason:run.reason,series:rows.slice(-120).map(({members,...c})=>c),series_note:'Latest 120 filtered held-out pairs; metrics use the complete filtered cohort. Wind metrics compare vector-blend speed against observed speed.'};
}
