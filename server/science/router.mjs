import {magnitude,mean,normalizeWeights,blendValues} from './metrics.mjs';
const scales={temperature:5,rainfall:25,wind_uv:5};
function errorStats(rows,model){const errors=rows.filter(c=>c.predictions[model]!==undefined).map(c=>{const p=c.predictions[model];return Array.isArray(p)?Math.hypot(p[0]-c.observed[0],p[1]-c.observed[1]):p-c.observed;});return {n:errors.length,rmse:errors.length?Math.sqrt(mean(errors.map(e=>e*e))):null};}
/** A transparent skill router: all statistics are fitted using observations available before cutoff. */
export function fitRouter(cases,{cutoff,variable,models,recentDays=30,strength=1}={}) {
  const cutoffMs=Date.parse(cutoff);
  if(!Number.isFinite(cutoffMs))throw Error('Router cutoff must be an explicit timestamp');
  const history=cases.filter(c=>c.variable===variable&&models.every(id=>c.predictions[id]!==undefined)&&Date.parse(c.observed_at)<cutoffMs&&Date.parse(c.valid_end)<cutoffMs&&Date.parse(c.issued_at)<cutoffMs);
  const groups=['state','district','region','season','lead_hours','regime'];
  const fitted={cutoff,variable,models,recentDays,strength,n:history.length,global:{},groups:{},recent:{},data_kind:history[0]?.data_kind||null};
  for(const model of models){fitted.global[model]=errorStats(history,model);fitted.recent[model]=errorStats(history.filter(c=>Date.parse(c.valid_end)>=cutoffMs-recentDays*86400000),model);}
  for(const group of groups){fitted.groups[group]={};for(const value of new Set(history.map(c=>c[group]).filter(v=>v!==undefined&&v!==null)))fitted.groups[group][value]=Object.fromEntries(models.map(model=>[model,errorStats(history.filter(c=>c[group]===value),model)]));}
  return fitted;
}
export function route(fitted,context,predictions) {
  const scale=scales[fitted.variable]||5,ids=fitted.models;
  const available=ids.filter(id=>predictions[id]!==undefined&&(Array.isArray(predictions[id])?predictions[id].length===2&&predictions[id].every(Number.isFinite):Number.isFinite(predictions[id])));
  const centre=mean(available.map(id=>magnitude(predictions[id])));
  // Require matched support for every competing model before comparing learned skill.
  const useSkill=available.length>0&&available.every(id=>fitted.global[id]?.n>=10);
  const explanations=ids.map(id=>{
    const terms=[];const prior=fitted.global[id];
    if(useSkill&&available.includes(id)) {
      const base=Math.max(prior.rmse,scale*.02);
      terms.push({feature:'Historical skill',effect:-Math.log(base/scale),n:prior.n,rmse:prior.rmse});
      for(const field of Object.keys(fitted.groups)){const stat=fitted.groups[field][context[field]]?.[id];if(stat?.n>=5)terms.push({feature:field,effect:-.18*(stat.n/(stat.n+20))*Math.log(Math.max(stat.rmse,scale*.02)/base),n:stat.n,rmse:stat.rmse,context:context[field]});}
      const recent=fitted.recent[id];if(recent?.n>=5)terms.push({feature:`Recent ${fitted.recentDays} days`,effect:-.3*(recent.n/(recent.n+20))*Math.log(Math.max(recent.rmse,scale*.02)/base),n:recent.n,rmse:recent.rmse});
      terms.push({feature:'Disagreement safeguard',effect:-.05*Math.min(3,Math.abs(magnitude(predictions[id])-centre)/scale),note:'Small fixed heuristic; disagreement does not prove a model is wrong.'});
    }
    const score=terms.reduce((sum,t)=>sum+t.effect,0)*fitted.strength;
    return {model:id,available:available.includes(id),score,terms,reason:!available.includes(id)?'Missing value: weight is zero.':useSkill?'Score uses only earlier matched forecast/observation pairs.':'No common historical skill evidence: equal eligible-source weights.'};
  });
  const peak=Math.max(0,...explanations.filter(e=>e.available).map(e=>e.score));
  const weights=normalizeWeights(Object.fromEntries(explanations.map(e=>[e.model,e.available?Math.exp(e.score-peak):0])),available);
  return {weights,explanations,basis:useSkill?'historical-context skill':'equal available sources',cutoff:fitted.cutoff,historical_cases:fitted.n,used_historical_skill:useSkill,...blendValues(predictions,weights)};
}
