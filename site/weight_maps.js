import {esc,fmt,ist,table,samplesFor,mapSVG,bindMap,kdeSVG} from './map_shared.js';
import {modelNames} from './blend_view.js';

const ids=['gfs_global','ecmwf_ifs025','ecmwf_aifs025_single','icon_global','gem_global','jma_gsm'];
const colors=['#146f9a','#b25a26','#7953a2','#318052','#b13f65','#555aa9'];
export const weightLabel=w=>!Number.isFinite(w)?'Unavailable':w===0?'Not contributing':`${fmt(w*100)}%`;

export async function renderWeightMaps(host,selection,geo){
  host.innerHTML='<p role="status">Loading six-model forecasts across the India sample grid…</p>';
  const results=await samplesFor(selection,'live',(done,total)=>{if(host.isConnected)host.innerHTML=`<p role="status">Live model coverage checked: ${done} of ${total} coordinates.</p>`;},geo);
  if(!host.isConnected)return;
  const samples=results.filter(r=>r.data?.series?.length);
  if(!samples.length){host.innerHTML='<h2>Live model weight map</h2><p>No coordinate forecasts returned. Retry using Refresh data above.</p>';return;}
  const times=[...new Set(samples.flatMap(s=>s.data.series.map(r=>r.time)))].sort();
  const state={selected:0,zoom:1,center:null};let source='all',variable='temperature',time=times[0];
  const draw=()=>{
    const rows=samples.map(s=>s.data.series.find(r=>r.time===time)?.[variable]),weights=rows.map(r=>r?.weights||{});
    const points=samples.flatMap((s,i)=>{
      const w=weights[i],dominant=ids.filter(id=>Number.isFinite(w[id])&&w[id]>0).sort((a,b)=>w[b]-w[a])[0],id=source==='all'?dominant:source;
      if(!id||!Number.isFinite(w[id])||w[id]<=0)return [];
      const strength=w[id],base=colors[ids.indexOf(id)];
      return [{index:i,label:s.label,...s.data.coordinates,selected:i===state.selected,value:`${modelNames[id]} · ${weightLabel(strength)}`,color:source==='all'?base:`color-mix(in srgb, ${base} ${20+80*strength}%, white)`,densityValue:strength}];
    });
    if(points.length&&!points.some(p=>p.index===state.selected))state.selected=points[0].index;
    for(const point of points)point.selected=point.index===state.selected;
    const selected=samples[state.selected],values=weights[state.selected]||{},density=points.map(p=>p.densityValue);
    const controls=`<div class="comparison-controls"><label class="input-field">Source<select id="weight-source"><option value="all">Largest contributor</option>${ids.map(id=>`<option value="${id}" ${source===id?'selected':''}>${esc(modelNames[id])}</option>`).join('')}</select></label><label class="input-field">Weather variable<select id="weight-variable">${[['temperature','Temperature'],['rainfall','Hourly rainfall'],['wind_uv','Wind vector']].map(([id,label])=>`<option value="${id}" ${variable===id?'selected':''}>${label}</option>`).join('')}</select></label><label class="input-field">Exact target time<select id="weight-time">${times.map(t=>`<option value="${t}" ${time===t?'selected':''}>${ist(t)}</option>`).join('')}</select></label></div>`;
    const bars=ids.map((id,i)=>`<div class="weight-source-row"><b>${esc(modelNames[id])}</b><meter min="0" max="1" value="${values[id]||0}" style="--series:${colors[i]}"></meter><span>${weightLabel(values[id])}</span></div>`).join('');
    host.innerHTML=`<header class="map-workspace-head"><div><span class="eyebrow">LIVE SOURCE CONTRIBUTIONS</span><h2>Where does each model contribute?</h2><p>Only coordinates with a returned value for this source, variable and hour appear. These weights describe an experimental blend, not verified skill.</p></div></header>${controls}<div class="risk-map-grid"><div class="risk-map-canvas">${mapSVG(geo,points,{zoom:state.zoom,center:state.center,idPrefix:'weight'})}${points.length?'<p>Colour shows the leading model, or the selected model’s contribution. Empty land has no returned value.</p>':'<p>No returned values for this source at the selected time.</p>'}<div class="science-legend">${ids.map((id,i)=>`<span><i style="background:${colors[i]}"></i>${esc(modelNames[id])}</span>`).join('')}</div>${kdeSVG(density,{label:source==='all'?'Leading model share':'Selected model share',unit:'%',color:source==='all'?'#147e87':colors[ids.indexOf(source)],domain:[0,1],displayScale:100})}</div><aside class="map-inspector"><span class="eyebrow">RETURNED COORDINATE</span><h3>${esc(selected.label)}</h3><p>${ist(time)}</p>${bars}<p>Half the allocation is equal across eligible sources. The rest favours values nearer the source median; this is not a measured accuracy ranking.</p></aside></div><h3>Exact source weights</h3><p>${points.length} locations have a valid mapped contribution. The table below contains only returned forecasts.</p>${table(['Coordinate',...ids.map(id=>esc(modelNames[id]))],points.map(p=>{const i=p.index,s=samples[i];return [`<button data-point="${i}">${esc(s.label)}</button>`,...ids.map((id,j)=>`<span class="weight-cell" style="background:color-mix(in srgb, ${colors[j]} ${Math.round((weights[i][id]||0)*100)}%, white)">${weightLabel(weights[i][id])}</span>`)];}))}<details><summary>Data coverage</summary><p>${samples.length} of ${results.length} requested coordinates returned forecasts; ${results.length-samples.length} were unavailable. Each source is checked independently at ${ist(time)}.</p>${table(['Returned coordinate','Future target hours','Retrieved'],samples.map(s=>[`${esc(s.label)}<br>${fmt(s.data.coordinates.latitude)}° N, ${fmt(s.data.coordinates.longitude)}° E`,s.data.series.length,ist(s.data.retrieved_at)]))}</details>`;
    host.querySelector('#weight-source').onchange=e=>{source=e.target.value;draw();};
    host.querySelector('#weight-variable').onchange=e=>{variable=e.target.value;draw();};
    host.querySelector('#weight-time').onchange=e=>{time=e.target.value;draw();};
    bindMap(host,state,draw);
  };
  draw();
}
