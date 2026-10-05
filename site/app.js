import { additiveForward, spatialForward, scenarioRows, mixtureFromFixedSources } from './gate.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const pct = value => `${(100 * value).toFixed(value < .001 ? 4 : 1)}%`;
const num = (value, places = 1) => Number(value).toLocaleString('en-IN', {maximumFractionDigits:places});
const title = value => String(value).replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
const sourceName = id => ({gfs:'NOAA GFS',gefs:'NOAA GEFS',graphcast_operational:'GraphCast',ifs:'ECMWF IFS',aifs:'ECMWF AIFS'}[id] || title(id));
const colors = ['#7461e9','#36baa7','#f09e6b','#5d8fe6','#d577a8'];
let demo, gate, catalogue, benchmark, currentCase, labTab = 'weights', liveCache = new Map();

function nav(view) {
  const allowed = ['overview','public','lab','evidence','atlas'];
  const name = allowed.includes(view) ? view : 'overview';
  for (const item of allowed) $(`view-${item}`).hidden = item !== name;
  document.querySelectorAll('#nav a').forEach(link => { link.classList.toggle('active', link.dataset.view === name); if (link.dataset.view === name) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
  $('page-title').textContent = ({overview:'Overview',public:'Live forecasts',lab:'Explainable blend',evidence:'Evidence',atlas:'Model atlas'})[name];
  if (name === 'public') loadPublic();
  window.scrollTo({top:0,behavior:'instant'});
}

function fillSelect(element, items, label) { element.innerHTML = items.map(item => `<option value="${esc(item)}">${esc(label(item))}</option>`).join(''); }
function setStatus(id, value, error=false) { $(id).textContent = value; $(id).classList.toggle('error',error); }
function stat(label, value, sub='') { return `<div class="stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`; }

function chooseLocation() {
  const variable = $('lab-variable').value;
  const locations = [...new Set(demo.cases.filter(item => item.variable === variable).map(item => item.location_id))];
  const prior = $('lab-location').value;
  fillSelect($('lab-location'),locations,title);
  $('lab-location').value = locations.includes(prior) ? prior : (locations.includes('mumbai') ? 'mumbai' : locations[0]);
  chooseCase();
}
function chooseCase() {
  const cases = demo.cases.filter(item => item.variable === $('lab-variable').value && item.location_id === $('lab-location').value);
  $('lab-case').innerHTML = cases.map((item,i) => `<option value="${i}">${esc(new Date(item.base.target.valid_start).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}))} · ${i===cases.length-1?'Latest example':'Prepared example'}</option>`).join('');
  $('lab-case').value = String(cases.length-1);
  resetCase();
}
function resetCase() {
  const cases = demo.cases.filter(item => item.variable === $('lab-variable').value && item.location_id === $('lab-location').value);
  currentCase = cases[Number($('lab-case').value)] || cases[0];
  if (!currentCase) return;
  const raw = currentCase.base.evidence.context_feature_values;
  $('input-lead').value = raw['lead.hours'];
  $('input-date').value = currentCase.base.target.valid_start.slice(0,10);
  $('input-coast').value = raw['geography.coast'];
  $('input-elevation').value = raw['geography.elevation'];
  $('input-situation').value = 'saved';
  const sources = gate.variables[currentCase.variable].sources;
  $('source-inputs').innerHTML = sources.map((id,i) => `<div class="source-editor" data-source="${esc(id)}"><label><input class="source-available" type="checkbox" checked> <span style="color:${colors[i%colors.length]}">●</span> ${esc(sourceName(id))} available</label><div class="editor-grid"><label class="input-field">Forecast signal<input class="source-signal" type="number" step="0.1" value="${esc(raw[`regime.source_${id}`])}"><small>Raw model value in target units.</small></label><label class="input-field">Recent error<input class="source-skill" type="number" min="0" max="20" step="0.01" value="${esc(raw[`recent_skill.${id}`])}"><small>Delayed normalized error; lower is better.</small></label><label class="input-field">Age, hours<input class="source-age" type="number" min="0" max="240" step="1" value="${esc(raw[`age.${id}`])}"><small>Hours since source initialization.</small></label><label class="input-field">Spread<input class="source-spread" type="number" min="0" max="1000" step="0.1" value="${esc(raw[`disagreement.spread_${id}`])}"><small>Member spread, if available.</small></label></div></div>`).join('');
  $('source-inputs').querySelectorAll('input').forEach(input => input.addEventListener('input', renderLab));
  fillSelect($('map-source'), sources, sourceName);
  renderLab();
}
function applyPattern() {
  const preset = $('input-situation').value;
  if (preset === 'saved') { resetCase(); return; }
  const editors = [...document.querySelectorAll('.source-editor')];
  const baseline = editors.map(editor => Number(currentCase.base.evidence.context_feature_values[`regime.source_${editor.dataset.source}`]));
  const center = baseline.reduce((a,b)=>a+b,0)/baseline.length;
  editors.forEach((editor,i) => {
    const value = preset === 'agreement' ? center : preset === 'disagreement' ? center + (i-(editors.length-1)/2)*Math.max(1,Math.abs(center)*.8) : baseline[i]*1.8;
    editor.querySelector('.source-signal').value = value.toFixed(2);
  });
  renderLab();
}
function formValues(spec) {
  const number = (id,low,high) => { const value=Number($(id).value); if (!$(id).value || !Number.isFinite(value) || value<low || value>high) throw Error(`Check ${$(id).closest('label')?.firstChild?.textContent.trim()||id}: enter ${low} to ${high}.`); return value; };
  const values={lead:number('input-lead',6,240),date:$('input-date').value,coast:number('input-coast',0,1000),elevation:number('input-elevation',-50,5000),sources:{}};
  for (const editor of document.querySelectorAll('.source-editor')) {
    const get = (cls,min,max) => { const el=editor.querySelector(cls),value=Number(el.value); if (!el.value || !Number.isFinite(value)||value<min||value>max) throw Error(`Check ${sourceName(editor.dataset.source)} ${cls.slice(8)}: use ${min} to ${max}.`); return value; };
    values.sources[editor.dataset.source]={available:editor.querySelector('.source-available').checked,signal:get('.source-signal',-1000,1000),skill:get('.source-skill',0,20),age:get('.source-age',0,240),spread:get('.source-spread',0,1000)};
  }
  if (!spec.sources.some(id=>values.sources[id]?.available)) throw Error('Keep at least one source available.');
  return values;
}
const featureLabel = key => ({'intercept':'learned source prior','lead.hours':'lead time','lead.duration':'target window','season.sin':'seasonal phase','season.cos':'seasonal phase','geography.latitude':'latitude','geography.longitude':'longitude','geography.elevation':'elevation','geography.coast':'coast distance','regime.predicted_mean':'model signal level','regime.predicted_max':'strongest model signal','disagreement.std':'model disagreement','interaction.lead_regime':'lead × predicted situation','interaction.latitude_season':'latitude × season'})[key] || (key.startsWith('recent_skill.') ? `${sourceName(key.split('.').at(-1))} recent error` : key.startsWith('regime.source_') ? `${sourceName(key.slice(14))} forecast signal` : key.startsWith('disagreement.spread_') ? `${sourceName(key.slice(20))} spread` : key.startsWith('age.') ? `${sourceName(key.slice(4))} age` : key.startsWith('availability.') ? `${sourceName(key.slice(13))} availability` : key.replaceAll('.',' '));
function relevantTerms(additive, sourceIndex, mask) {
  const sourceTerms=additive.terms[sourceIndex];
  const keys=Object.keys(sourceTerms);
  return keys.map(key=>({key, value:sourceTerms[key]-additive.terms.reduce((sum,item,i)=>sum+(mask[i]?additive.weights[i]*item[key]:0),0)})).filter(item=>Math.abs(item.value)>1e-5).sort((a,b)=>Math.abs(b.value)-Math.abs(a.value));
}
function weightRows(spec, result, mask, additive=null) {
  return spec.sources.map((id,i)=>{
    const top=additive ? relevantTerms(additive,i,mask).slice(0,2) : [];
    const reason=!mask[i]?'Excluded by your availability choice.':additive ? (top.length ? `Relative score: ${top.map(term=>`${featureLabel(term.key)} ${term.value>=0?'+':'−'}${num(Math.abs(term.value),2)}`).join('; ')}.`:'The fitted baseline dominates this source’s score.') : 'A trained network score after the four-site context and availability mask.';
    return `<div class="weight-item"><div class="weight-top"><strong><span style="color:${colors[i%colors.length]}">●</span> ${esc(sourceName(id))}</strong><b>${pct(result.weights[i])}</b></div><div class="weight-track"><div class="weight-fill" style="width:${Math.max(0,Math.min(100,result.weights[i]*100))}%;background:${colors[i%colors.length]}"></div></div><p>${esc(reason)}</p></div>`;
  }).join('');
}
function renderEvents(events, sources) {
  return events.length ? events.map(event=>`<div class="event-row"><strong>Above ${num(event.threshold)} ${currentCase.base.summary.units} · ${pct(event.probability)}</strong><small>${sources.map(id=>`${sourceName(id)} ${pct(event.contributions[id]||0)}`).join(' · ')}</small></div>`).join('') : '<p>No event thresholds are recorded for this example.</p>';
}
function renderTerms(spec, result, mask) {
  const entries=spec.sources.map((id,i)=>({id,i})).filter(item=>mask[item.i]);
  return entries.map(({id,i})=>{
    const terms=relevantTerms(result,i,mask).slice(0,4);
    return `<div class="event-row"><strong>${esc(sourceName(id))} · score ${num(result.logits[i],3)}</strong><small>Compared with the current weighted source average</small>${terms.map(term=>`<div class="term-row"><span>${esc(featureLabel(term.key))}</span><b class="${term.value>=0?'positive':'negative'}">${term.value>=0?'+':'−'}${num(Math.abs(term.value),3)}</b></div>`).join('')}</div>`;
  }).join('');
}
const sensitivityGroups={
  'Lead time':['lead.hours','interaction.lead_regime'],
  'Season':['season.sin','season.cos','interaction.latitude_season'],
  'Place':['geography.latitude','geography.longitude','geography.elevation','geography.coast'],
  'Weather situation':['regime.predicted_mean','regime.predicted_max','regime.source_','regime.cape','regime.u850','regime.v850','regime.q850','regime.mslp','regime.moisture_flux','regime.vorticity700'],
  'Model disagreement':['disagreement.std','disagreement.spread_'],
  'Recent error':['recent_skill.'],
  'Availability':['availability.']
};
function renderSensitivity(spec, rows, mask, neural) {
  const names=spec.additive.features.names, means=spec.additive.features.mean;
  const groupResults=Object.entries(sensitivityGroups).map(([label,prefixes])=>{
    const edited=rows.map(row=>({...row}));
    for (const row of edited) names.forEach((name,i)=>{if(prefixes.some(prefix=>name===prefix||name.startsWith(prefix)))row[name]=means[i];});
    const compare=spatialForward(spec,edited,mask);
    return {label,deltas:neural.weights.map((weight,i)=>weight-compare.weights[i])};
  });
  return spec.sources.map((id,i)=>{
    if (!mask[i]) return `<div class="event-row"><strong>${esc(sourceName(id))}</strong><small>Excluded: the availability mask fixes its weight at zero.</small></div>`;
    const top=groupResults.map(group=>({label:group.label,delta:group.deltas[i]})).sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta)).slice(0,3);
    return `<div class="event-row"><strong>${esc(sourceName(id))} · ${pct(neural.weights[i])}</strong><small>${top.map(item=>`${item.label}: ${item.delta>=0?'+':'−'}${pct(Math.abs(item.delta))}`).join(' · ')}</small><small>Difference from replacing that input group with training mean; other groups and source mask held fixed.</small></div>`;
  }).join('');
}
function renderNetwork(spec, neural, mask) {
  const coords=[80,280,490,700,910];
  const labels=['4 nearby sites','169 basis features','32 hidden channels','4 attention heads','source weights'];
  const circles=coords.map((x,i)=>`<g><rect x="${x-72}" y="85" width="145" height="125" rx="15" fill="${i===4?'#e9fff8':'#f0efff'}" stroke="${i===4?'#60cdb6':'#afa5f4'}"/><text x="${x}" y="125" text-anchor="middle" font-size="13" font-weight="800" fill="#3c3c75">${labels[i]}</text><text x="${x}" y="154" text-anchor="middle" font-size="11" fill="#687590">${['spatial context','centered splines','learned projection','self-attention + FFN','masked softmax'][i]}</text></g>`).join('');
  const edges=coords.slice(0,-1).map((x,i)=>`<path d="M${x+73} 148 L${coords[i+1]-73} 148" stroke="#8a78e8" stroke-width="3" marker-end="url(#arrow)"/>`).join('');
  const output=spec.sources.map((id,i)=>`${sourceName(id)} ${pct(neural.weights[i])}`).join(' · ');
  $('network-svg').innerHTML=`<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#8a78e8"/></marker></defs>${edges}${circles}<text x="520" y="250" text-anchor="middle" font-size="12" fill="#65758c">${esc(output)}</text>`;
  $('network-method').innerHTML=`<strong>Network calculation:</strong> exported PyTorch checkpoint, one encoder layer, four heads and ${spec.sources.length} source logits. The same edited context feeds both views. ${mask.filter(Boolean).length} source${mask.filter(Boolean).length===1?' is':'s are'} eligible. This neural path is a research challenger; the additive route is the saved accuracy champion.`;
}
function renderMap(spec) {
  const id=$('map-source').value || spec.sources[0];
  const cases=demo.cases.filter(item=>item.variable===currentCase.variable && item.location_id!==undefined).filter((item,i,all)=>all.findIndex(other=>other.location_id===item.location_id)===i);
  const x=lon=>115+(lon-72)*35, y=lat=>288-(lat-11)*14;
  const dots=cases.map(item=>{
    const source=item.base.sources.find(s=>s.source_id===id),weight=source?.weight||0,cx=x(item.base.target.longitude),cy=y(item.base.target.latitude);
    return `<g><circle cx="${cx}" cy="${cy}" r="${8+weight*22}" fill="#7563e8" fill-opacity="${.25+weight*.7}" stroke="#fff" stroke-width="2"/><text x="${cx}" y="${cy+37}" text-anchor="middle" font-size="12" font-weight="700" fill="#53627f">${esc(title(item.location_id))} ${pct(weight)}</text></g>`;
  }).join('');
  $('weight-map').innerHTML=`<path d="M90 285 Q160 115 405 56 Q630 74 790 250" fill="none" stroke="#c9d3e7" stroke-width="3" stroke-dasharray="7 7"/>${dots}`;
  $('map-range').textContent=`${sourceName(id)} across ${cases.length} saved locations. Prepared examples can have different dates and leads.`;
}
function renderLab() {
  if (!currentCase) return;
  try {
    const spec=gate.variables[currentCase.variable], values=formValues(spec), rows=scenarioRows(currentCase,values,spec.sources),mask=spec.sources.map(id=>values.sources[id].available);
    const additive=additiveForward(spec,rows[0],mask),neural=spatialForward(spec,rows,mask);
    const mixture=mixtureFromFixedSources(currentCase,spec.sources,additive.weights);
    const units=currentCase.base.summary.units;
    $('lab-stats').innerHTML=stat(currentCase.variable==='wind_uv'?'Vector mean speed':'Blended mean',`${num(mixture.mean,2)} ${units}`,'Using fixed calibrated source distributions')+stat('Eligible sources',`${mask.filter(Boolean).length} / ${mask.length}`,'Unavailable sources receive zero weight')+stat('Leading source',sourceName(spec.sources[additive.weights.indexOf(Math.max(...additive.weights))]),'Highest additive mixture share')+stat('Lead time',`${values.lead} h`,'Scenario target from model initialization');
    $('weight-bars').innerHTML=weightRows(spec,additive,mask,additive);
    $('lab-events').innerHTML=renderEvents(mixture.events,spec.sources);
    $('lab-terms').innerHTML=renderTerms(spec,additive,mask);
    $('lab-ledger').innerHTML=spec.sources.map((id,i)=>`<div class="ledger-row"><strong>${esc(sourceName(id))}</strong><span>${mask[i]?'Eligible · calibrated source present':'Unavailable · zero weight'}</span></div>`).join('')+`<div class="ledger-row"><strong>Other atlas candidates</strong><span>Outside this trained source set; not silently added</span></div>`;
    $('neural-weights').innerHTML=weightRows(spec,neural,mask);
    $('neural-reasons').innerHTML=renderSensitivity(spec,rows,mask,neural);
    renderNetwork(spec,neural,mask);renderMap(spec);
    $('lab-receipt').textContent=`Receipt ${currentCase.base.receipt_id} · model ${gate.model_digest.slice(0,12)} · ${currentCase.patch_locations.join(', ')} spatial context · generated research case · source distributions fixed while inputs change.`;
    setStatus('lab-message','');
  } catch(error) { setStatus('lab-message',error.message,true); }
}

function renderEvidence() {
  const variable=$('evidence-variable').value,metric=$('evidence-metric').value;
  const data=benchmark.variables[variable], methods=Object.entries(data.methods).filter(([,record])=>Number.isFinite(record[metric])).sort((a,b)=>a[1][metric]-b[1][metric]);
  const best=methods[0]?.[1][metric]||1,max=methods.at(-1)?.[1][metric]||1;
  const metrics=({proper_score:'Distribution score',mae:'Mean absolute error',rmse:'Root mean square error'});
  $('evidence-content').innerHTML=`<div class="metric-lead">${stat('Held-out cases',data.split_counts.test,'Chronologically separated generated examples')}${stat('Compared methods',methods.length,'Adaptive gates and statistical baselines')}${stat('Best displayed score',num(best,3),'Lower is better for this metric')}</div><article class="card"><div class="eyebrow">METHOD COMPARISON</div><h2>${esc(metrics[metric])}</h2><p>Each row uses the same held-out case count. Switch target or metric to inspect a different comparison.</p>${methods.map(([id,item],i)=>`<div class="method-row"><strong>${i+1}. ${esc(title(id))}</strong><b>${num(item[metric],3)}</b><meter min="0" max="${max*1.05}" value="${item[metric]}"></meter></div>`).join('')}</article><div class="two-columns"><article class="card"><div class="eyebrow">HOW TO READ THIS</div><h2>Lower scores mean closer forecasts</h2><p>The distribution score rewards both location and uncertainty. MAE and RMSE assess point summaries. They answer different questions, so a method can lead one metric and trail another.</p></article><article class="card"><div class="eyebrow">EVALUATION DESIGN</div><h2>Traceable splits</h2><p>Training ${data.split_counts.training}, cross-fit ${data.split_counts.cross_fitted}, development ${data.split_counts.development}, held-out test ${data.split_counts.test}. The benchmark stays separate from current provider forecasts.</p></article></div>`;
}
function modelFamily(item) { return item.model_class==='nwp'?'nwp':item.model_class==='ai'?'ai':'hybrid'; }
function renderAtlas() {
  const query=$('model-search').value.trim().toLowerCase(),family=$('model-family').value,variable=$('model-variable').value;
  const filtered=catalogue.models.filter(item=>(family==='all'||modelFamily(item)===family)&&(variable==='all'||item.capabilities?.[variable]===true)&&(!query||[item.name,item.provider,item.description,...Object.values(item.use_context||{})].join(' ').toLowerCase().includes(query)));
  $('model-count').textContent=`${filtered.length} / ${catalogue.models.length} profiles`;
  $('model-grid').innerHTML=filtered.map(item=>`<article class="model-card"><div class="model-meta"><span>${esc(modelFamily(item))}</span><span>${esc(item.provider||'Research')}</span></div><h2>${esc(item.name)}</h2><p>${esc(item.description||item.purpose)}</p><div class="context-grid">${[['Region','region'],['Season','season'],['Lead time','lead'],['Weather situation','weather']].map(([label,key])=>`<div class="context-cell"><strong>${label}</strong><span>${esc(item.use_context?.[key]||'Context requires independent validation.')}</span></div>`).join('')}</div><button type="button" data-model="${esc(item.id)}">View full analysis ↗</button></article>`).join('')||'<p>No models match these filters.</p>';
  $('model-grid').querySelectorAll('[data-model]').forEach(button=>button.addEventListener('click',()=>openModel(button.dataset.model)));
}
function openModel(id) {
  const item=catalogue.models.find(model=>model.id===id);if(!item)return;
  const context=item.use_context||{};
  const links=(item.evidence_urls||[]).filter(url=>/^https:\/\//.test(url)).slice(0,5);
  $('model-detail').innerHTML=`<div class="eyebrow">MODEL ANALYSIS · ${esc(item.provider||'Research')}</div><h1>${esc(item.name)}</h1><p>${esc(item.description||item.purpose)}</p><div class="model-meta"><span>${esc(modelFamily(item))}</span><span>${esc(item.architecture||'Architecture documented in source')}</span></div><h2 style="margin-top:25px">When should it be considered?</h2><div class="detail-grid">${[['Region',context.region],['Season',context.season],['Lead time',context.lead],['Weather situation',context.weather]].map(([label,text])=>`<div class="detail-box"><strong>${label}</strong>${esc(text||'Requires local validation.')}</div>`).join('')}</div><h2 style="margin-top:25px">Deeper analysis</h2><p>${esc(context.analysis||item.purpose)}</p><div class="detail-grid"><div class="detail-box"><strong>Strengths</strong><ul class="detail-list">${(item.pros||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></div><div class="detail-box"><strong>Limits and conditions</strong><ul class="detail-list">${(item.cons||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></div></div><p class="fineprint">${esc(item.assessment_basis||'Suitability profile, not a measured local skill ranking.')}</p>${links.length?`<h3>Primary references</h3><ul class="detail-list">${links.map((url,i)=>`<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Provider / author source ${i+1} ↗</a></li>`).join('')}</ul>`:''}`;
  $('model-dialog').showModal();
}

async function loadPublic() {
  const city=$('public-city').value;
  if(!city)return;
  if(liveCache.has(city)){renderPublic(liveCache.get(city));return;}
  setStatus('public-message','Retrieving current provider forecasts…');
  try {
    const response=await fetch(`/api/public-forecast?city=${encodeURIComponent(city)}`);
    const data=await response.json();
    if(!response.ok||data.data_kind!=='real'||!Array.isArray(data.rows))throw Error(data.error||'Provider response could not be read');
    liveCache.set(city,data);renderPublic(data);
  }catch(error){$('public-content').hidden=true;setStatus('public-message',`Live forecast unavailable: ${error.message}`,true);}
}
function renderPublic(data) {
  const variable=$('public-variable').value,horizon=Number($('public-horizon').value),ids=Object.keys(data.models),time=[...new Set(data.rows.map(row=>row.valid_time))].slice(0,horizon),rows=ids.map(id=>time.map(t=>data.rows.find(row=>row.model===id&&row.valid_time===t)?.[variable]??null));
  const all=rows.flat().filter(Number.isFinite);
  if (!all.length) { $('public-content').hidden=true;setStatus('public-message','The provider returned no usable values for this variable and horizon.',true);return; }
  const min=Math.min(...all),max=Math.max(...all),range=Math.max(1,max-min),unit=data.units[variable];
  $('public-content').hidden=false;setStatus('public-message',`Retrieved ${new Date(data.retrieved_at).toLocaleString('en-IN')} · ${data.attribution}`);
  $('public-stats').innerHTML=stat('Current models',ids.length,'Named GFS, IFS and AIFS sources')+stat('Hourly targets',time.length,'Consecutive UTC target times')+stat('Observed range',`${num(min)}–${num(max)} ${unit}`,'Across displayed model forecasts')+stat('Latest update',new Date(data.retrieved_at).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}),'Provider retrieval time');
  $('public-chart-title').textContent=`${({precipitation:'Hourly precipitation',temperature_2m:'2 m temperature',wind_speed_10m:'10 m wind speed'})[variable]} · ${data.city}`;
  $('public-legend').innerHTML=ids.map((id,i)=>`<span><i style="background:${colors[i]}"></i>${esc(data.models[id].name)}</span>`).join('');
  const paths=rows.map((series,j)=>series.map((value,i)=>value===null?null:[42+i*(810/Math.max(time.length-1,1)),255-(value-min)/range*210]).filter(Boolean).map((point,i)=>`${i?'L':'M'}${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join('')).map((path,i)=>`<path d="${path}" fill="none" stroke="${colors[i]}" stroke-width="3" stroke-linejoin="round"/>`).join('');
  const guides=[0,.25,.5,.75,1].map(frac=>`<line x1="42" x2="860" y1="${255-frac*210}" y2="${255-frac*210}" stroke="#e7ebf5"/><text x="36" y="${259-frac*210}" text-anchor="end" font-size="11" fill="#8a96aa">${num(min+frac*range,1)}</text>`).join('');
  $('public-chart').innerHTML=`${guides}${paths}`;
  $('public-chart-caption').textContent=`${time[0]} through ${time.at(-1)} · ${unit} · separate forecast sources, not blended`;
  $('public-table').querySelector('thead').innerHTML=`<tr><th>Valid UTC</th>${ids.map(id=>`<th>${esc(data.models[id].name)}</th>`).join('')}</tr>`;
  $('public-table').querySelector('tbody').innerHTML=time.slice(0,12).map((t,i)=>`<tr><td>${esc(t.slice(5,16).replace('T',' '))}</td>${rows.map(series=>`<td>${series[i]===null?'—':num(series[i],1)}</td>`).join('')}</tr>`).join('');
  const receipt=[['Provider','Open-Meteo'],['City',data.city],['Retrieved',data.retrieved_at],['Requested coordinates',`${data.requested_coordinates.latitude}, ${data.requested_coordinates.longitude}`],['Models',ids.map(id=>data.models[id].name).join(', ')],['Record hash',data.sha256.slice(0,16)+'…']];
  $('public-receipt').innerHTML=receipt.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
}

async function start() {
  document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>{location.hash=button.dataset.go;}));
  window.addEventListener('hashchange',()=>nav(location.hash.slice(1)));
  const urls=['/data/demo.json','/data/gate.json','/data/catalogue.json','/data/benchmark.json'];
  try {
    const responses=await Promise.all(urls.map(url=>fetch(url)));
    if(responses.some(response=>!response.ok))throw Error('A research data file could not be loaded');
    [demo,gate,catalogue,benchmark]=await Promise.all(responses.map(response=>response.json()));
    if(demo.model_digest!==gate.model_digest||benchmark.source_model_digest!==gate.model_digest)throw Error('Model and evidence versions do not match');
    fillSelect($('public-city'),['Mumbai','Delhi','Bengaluru','Chennai','Kolkata','Hyderabad','Guwahati','Jaipur'],x=>x);
    $('public-city').value='Mumbai';
    $('lab-variable').addEventListener('change',chooseLocation);$('lab-location').addEventListener('change',chooseCase);$('lab-case').addEventListener('change',resetCase);
    ['input-lead','input-date','input-coast','input-elevation','map-source'].forEach(id=>$(id).addEventListener('input',renderLab));
    $('input-situation').addEventListener('change',applyPattern);$('reset-scenario').addEventListener('click',resetCase);
    document.querySelectorAll('[data-lab-tab]').forEach(button=>button.addEventListener('click',()=>{labTab=button.dataset.labTab;$('panel-weights').hidden=labTab!=='weights';$('panel-network').hidden=labTab!=='network';document.querySelectorAll('[data-lab-tab]').forEach(tab=>tab.setAttribute('aria-selected',String(tab===button)));}));
    ['evidence-variable','evidence-metric'].forEach(id=>$(id).addEventListener('change',renderEvidence));
    ['model-search','model-family','model-variable'].forEach(id=>$(id).addEventListener('input',renderAtlas));
    ['public-city','public-variable','public-horizon'].forEach(id=>$(id).addEventListener('change',()=>$('public-city').id===id?loadPublic():liveCache.has($('public-city').value)?renderPublic(liveCache.get($('public-city').value)):loadPublic()));
    $('close-model').addEventListener('click',()=>$('model-dialog').close());
    chooseLocation();renderEvidence();renderAtlas();nav(location.hash.slice(1));
  } catch(error) { setStatus('lab-message',`Research data unavailable: ${error.message}`,true);setStatus('public-message','Live forecast desk is still available; choose a city to retry.');nav(location.hash.slice(1)); }
}
start();
