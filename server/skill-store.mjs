import {readFile} from 'node:fs/promises';
import {walkForward,summarizeValidation} from './science/validation.mjs';
const archives=new Map(),runs=new Map();
export function validateArchive(archive,kind){
  if(!Array.isArray(archive.cases)||archive.cases.length>100000)throw Error('Invalid or oversized skill archive');
  const seen=new Set();
  for(const c of archive.cases){
    const vector=c.variable==='wind_uv',valid=v=>vector?Array.isArray(v)&&v.length===2&&v.every(Number.isFinite):Number.isFinite(v)&&(c.variable!=='rainfall'||v>=0);
    if(seen.has(c.id)||c.data_kind!==kind||!['temperature','rainfall','wind_uv'].includes(c.variable)||!valid(c.observed)||!c.predictions||!Object.values(c.predictions).every(valid))throw Error('Invalid paired evidence');
    seen.add(c.id);
    if(['issued_at','valid_start','valid_end','observed_at'].some(k=>!Number.isFinite(Date.parse(c[k])))||Date.parse(c.observed_at)<Date.parse(c.valid_end)||Date.parse(c.valid_start)<Date.parse(c.issued_at)||Date.parse(c.valid_end)<Date.parse(c.valid_start))throw Error('Invalid paired timestamps');
    if(c.units!==({temperature:'degC',rainfall:'mm',wind_uv:'m/s'})[c.variable]||!Number.isFinite(c.lead_hours)||c.lead_hours<0)throw Error('Invalid target units or lead');
  }
  return archive;
}
export async function loadArchive(mode='real') {
  if(!['real','research'].includes(mode))throw Error('Unknown evidence mode');
  if(!archives.has(mode))archives.set(mode,readFile(new URL(`./data/${mode==='research'?'research':'real'}-validation.json`,import.meta.url),'utf8').then(JSON.parse).then(a=>validateArchive(a,mode==='real'?'real':'synthetic')).catch(e=>{if(e.code==='ENOENT'&&mode==='real')return {data_kind:'real',source:'No verified observation archive connected',cases:[]};archives.delete(mode);throw e;}));
  const archive=await archives.get(mode);
  if(archive.cases.some(c=>c.data_kind!==(mode==='real'?'real':'synthetic')))throw Error('Archive provenance does not match selected mode');
  return archive;
}
export async function skillReport(params) {
  const mode=params.get('mode')||'real',variable=params.get('variable')||'temperature',recentDays=Number(params.get('recent')||30);
  if(!['temperature','rainfall','wind_uv'].includes(variable)||![7,30,90].includes(recentDays))throw Error('Unsupported variable or recent window');
  const archive=await loadArchive(mode),key=`${mode}:${variable}:${recentDays}`;
  if(!runs.has(key))runs.set(key,walkForward(archive.cases,{variable,recentDays}));
  const filters=Object.fromEntries(params);filters.variable=variable;
  return {data_kind:archive.data_kind,evidence_label:mode==='real'?'REAL OBSERVATIONS':'SIMULATED RESEARCH EXAMPLE',source:archive.source,filters,options:Object.fromEntries(['state','district','region','season','month','lead_hours','regime','location_id'].map(k=>[k,[...new Set(archive.cases.filter(c=>c.variable===variable).map(c=>c[k]))].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}))])),...summarizeValidation(runs.get(key),filters,recentDays)};
}
