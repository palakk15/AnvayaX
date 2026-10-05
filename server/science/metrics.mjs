// Verification on a common paired cohort. Undefined statistics stay null, never zero.
export const mean = values => values.length ? values.reduce((a,b)=>a+b,0)/values.length : null;
const ratio=(a,b)=>b?a/b:null;
export function magnitude(value){return Array.isArray(value)?Math.hypot(...value):value;}
export function deterministicMetrics(pairs) {
  const valid=pairs.filter(p=>Number.isFinite(p.forecast)&&Number.isFinite(p.observed));
  if(!valid.length)return {n:0,mae:null,rmse:null,bias:null,correlation:null};
  const x=valid.map(p=>p.forecast),y=valid.map(p=>p.observed),mx=mean(x),my=mean(y),errors=x.map((v,i)=>v-y[i]);
  const covariance=x.reduce((s,v,i)=>s+(v-mx)*(y[i]-my),0);
  const denom=Math.sqrt(x.reduce((s,v)=>s+(v-mx)**2,0)*y.reduce((s,v)=>s+(v-my)**2,0));
  return {n:valid.length,mae:mean(errors.map(Math.abs)),rmse:Math.sqrt(mean(errors.map(v=>v*v))),bias:mean(errors),correlation:valid.length>1&&denom>1e-12?covariance/denom:null};
}
export function eventMetrics(pairs,threshold) {
  let hits=0,misses=0,falseAlarms=0,correctNegatives=0;
  const valid=pairs.filter(p=>Number.isFinite(p.forecast)&&Number.isFinite(p.observed));
  for(const p of valid){const f=p.forecast>threshold,o=p.observed>threshold;if(f&&o)hits++;else if(f)falseAlarms++;else if(o)misses++;else correctNegatives++;}
  const probabilities=valid.filter(p=>Number.isFinite(p.probability)&&p.probability>=0&&p.probability<=1);
  const reliability=Array.from({length:10},(_,i)=>{const bin=probabilities.filter(p=>Math.min(9,Math.floor(p.probability*10))===i);return {lower:i/10,upper:(i+1)/10,n:bin.length,forecast_probability:mean(bin.map(p=>p.probability)),observed_frequency:mean(bin.map(p=>Number(p.observed>threshold)))};});
  return {hits,misses,false_alarms:falseAlarms,correct_negatives:correctNegatives,precision:ratio(hits,hits+falseAlarms),recall:ratio(hits,hits+misses),pod:ratio(hits,hits+misses),far:ratio(falseAlarms,hits+falseAlarms),csi:ratio(hits,hits+misses+falseAlarms),f1:ratio(2*hits,2*hits+falseAlarms+misses),brier:mean(probabilities.map(p=>(p.probability-Number(p.observed>threshold))**2)),probability_n:probabilities.length,reliability};
}
export function normalizeWeights(values,available=Object.keys(values)) {
  const entries=Object.entries(values).filter(([id,v])=>available.includes(id)&&Number.isFinite(v)&&v>0),sum=entries.reduce((s,[,v])=>s+v,0);
  return Object.fromEntries(Object.keys(values).map(id=>[id,sum&&available.includes(id)&&Number.isFinite(values[id])&&values[id]>0?values[id]/sum:0]));
}
export function blendValues(predictions,weights) {
  const available=Object.keys(predictions).filter(id=>Array.isArray(predictions[id])?predictions[id].length===2&&predictions[id].every(Number.isFinite):Number.isFinite(predictions[id]));
  const w=normalizeWeights(weights,available);
  if(!Object.values(w).some(v=>v>0))return {value:null,weights:w};
  const vector=Array.isArray(predictions[available[0]]);
  if(available.some(id=>Array.isArray(predictions[id])!==vector))throw Error('Cannot mix vectors and scalar forecasts');
  const value=vector?[0,1].map(j=>available.reduce((sum,id)=>sum+predictions[id][j]*(w[id]||0),0)):available.reduce((sum,id)=>sum+predictions[id]*(w[id]||0),0);
  const speed=vector?Math.hypot(...value):null;
  return {value,weights:w,...(vector?{speed,direction:speed<1e-8?null:(Math.atan2(-value[0],-value[1])*180/Math.PI+360)%360}:{}),renormalized:available.length<Object.keys(weights).length};
}
export function ensembleProbability(members,threshold){const values=members?.map(magnitude).filter(Number.isFinite)||[];return values.length>=2?values.filter(v=>v>threshold).length/values.length:null;}
export function quantile(values,p){const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);if(!sorted.length)return null;const x=(sorted.length-1)*p,lo=Math.floor(x);return sorted[lo]+(sorted[Math.ceil(x)]-sorted[lo])*(x-lo);}
