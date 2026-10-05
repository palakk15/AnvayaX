import {renderLiveAnalytics,networkInspector} from './analytics.js';
import { additiveForward, spatialForward, scenarioRows, mixtureFromFixedSources } from './gate.js';
import { futureTargetTimes, formatIST, modelWindow } from './live_view.js';
import {readJSON, weatherLabels, weatherHelp, weatherDescription, downloadForecast} from './weather.js';
import {setupChat} from './chat.js';
import {setupPlatform,openPlatform,platformViews} from './platform.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const pct = value => `${(100 * value).toFixed(value < .001 ? 4 : 1)}%`;
const num = (value, places = 1) => Number(value).toLocaleString('en-IN', {maximumFractionDigits:places});
const title = value => String(value).replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
const sourceName = id => ({gfs:'NOAA GFS',gefs:'NOAA GEFS',graphcast_operational:'GraphCast',ifs:'ECMWF IFS',aifs:'ECMWF AIFS'}[id] || title(id));
const colors = ['#087f8c','#b66a18','#635bce','#bf5670','#417f4b','#354c98'];
const profileId=id=>({ifs:'ifs_control',aifs:'aifs_single',gfs_global:'gfs',ecmwf_ifs025:'ifs_control',ecmwf_aifs025_single:'aifs_single'}[id]||id);
const profileLink=(id,label)=>['gem_global','jma_gsm'].includes(id)?`<span>${esc(label||sourceName(id))}</span>`:`<a href="#atlas" class="model-link" data-profile="${esc(profileId(id))}">${esc(label||sourceName(id))}</a>`;
let demo, gate, catalogue, benchmark, currentCase, labTab = 'weights';
let liveSelection = null;
const liveCache = new Map();
const researchLoads = new Map();
let labReady = false;
let routeSerial = 0;

async function nav(view) {
  const allowed = ['overview','public','atlas',...platformViews];
  const neural=view==='lab'||view==='maps/neural';
  const name = neural?'maps':allowed.includes(view) ? view : 'overview';
  if(view==='lab')history.replaceState(null,'','#maps/neural');
  document.body.dataset.workspace=name;
  $('neural-studio').hidden=!neural;
  $('maps-body').hidden=neural;
  $('maps-status').hidden=neural;
  $('maps-tab-geography').setAttribute('aria-current',neural?'false':'page');
  $('maps-tab-neural').setAttribute('aria-current',neural?'page':'false');
  window.anvayaContext={view:name};
  const serial = ++routeSerial;
  for (const item of allowed) $(`view-${item}`).hidden = item !== name;
  document.querySelectorAll('#nav a').forEach(link => { link.classList.toggle('active', link.dataset.view === name); if (link.dataset.view === name) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
  window.scrollTo({top:0,behavior:'auto'});
  try {
    if (platformViews.includes(name)&&!neural) await openPlatform(name);
    if (name === 'public') await loadPublic();
    if (neural) {
      setStatus('lab-message','Loading trained research examples…');
      [demo,gate] = await Promise.all([loadResearch('demo'),loadResearch('gate')]);
      if (demo.model_digest !== gate.model_digest) throw Error('The saved research artifacts do not match.');
      if (serial === routeSerial && !labReady) { chooseLocation(); labReady = true; }
      if (serial === routeSerial) setStatus('lab-message','');
    }
    if (name === 'atlas') { catalogue = await loadResearch('catalogue'); if (serial === routeSerial) renderAtlas(); }
  } catch(error) {
    if (serial !== routeSerial) return;
    if (neural) setStatus('lab-message',`Research examples unavailable: ${error.message}`,true);
    if (name === 'atlas') $('model-grid').textContent=`Model profiles unavailable: ${error.message}`;
  }
}

async function loadResearch(name) {
  if (!researchLoads.has(name)) researchLoads.set(name,fetch(`/data/${name}.json`).then(response=>{
    if (!response.ok) throw Error(`${name} returned ${response.status}`);
    return response.json();
  }).catch(error=>{researchLoads.delete(name);throw error;}));
  return researchLoads.get(name);
}

function fillSelect(element, items, label) { element.innerHTML = items.map(item => `<option value="${esc(item)}">${esc(label(item))}</option>`).join(''); }
function setStatus(id, value, error=false) { $(id).textContent = value; $(id).classList.toggle('error',error); }
function stat(label, value, sub='') { return `<div class="stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`; }

function chooseLocation() {
  const variable = $('lab-variable').value;
  const locations = [...new Set(demo.cases.filter(item => item.variable === variable).map(item => item.location_id))];
  const prior = $('lab-location').value;
  fillSelect($('lab-location'),locations,title);
  $('lab-location').insertAdjacentHTML('afterbegin','<option value="">Choose a research site</option>');
  const chosen=liveSelection?.label.split(',')[0].toLowerCase();
  $('lab-location').value = locations.includes(prior)?prior:locations.includes(chosen)?chosen:'';
  chooseCase();
}
function chooseCase() {
  document.querySelector('.lab-layout').hidden=!$('lab-location').value;
  if(!$('lab-location').value){currentCase=null;$('lab-case').innerHTML='<option>Select a site first</option>';return;}
  const cases = demo.cases.filter(item => item.variable === $('lab-variable').value && item.location_id === $('lab-location').value);
  $('lab-case').innerHTML = cases.map((item,i) => `<option value="${i}">${esc(new Date(item.base.target.valid_start).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}))} · ${item.selection.startsWith('illustrative')?'Illustrative high-rain case':'Latest prepared case'}</option>`).join('');
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
  $('source-inputs').innerHTML = sources.map((id,i) => `<div class="source-editor" data-source="${esc(id)}"><label><input class="source-available" type="checkbox" checked> <span style="color:${colors[i%colors.length]}">●</span> ${esc(sourceName(id))} available</label><div class="editor-grid"><label class="input-field">Router signal<input class="source-signal" type="number" step="0.1" value="${esc(raw[`regime.source_${id}`])}"><small>Context input; source distribution stays fixed.</small></label><label class="input-field">Recent error<input class="source-skill" type="number" min="0" max="20" step="0.01" value="${esc(raw[`recent_skill.${id}`])}"><small>Delayed normalized error; lower is better.</small></label><label class="input-field">Age, hours<input class="source-age" type="number" min="0" max="240" step="1" value="${esc(raw[`age.${id}`])}"><small>Age input; fitted effect can be zero.</small></label><label class="input-field">Spread<input class="source-spread" type="number" min="0" max="1000" step="0.1" value="${esc(raw[`disagreement.spread_${id}`])}"><small>Member spread, if available.</small></label></div></div>`).join('');
  $('source-inputs').querySelectorAll('input').forEach(input => input.addEventListener('input', renderLab));
  fillSelect($('map-source'), sources, sourceName);
  renderLab();
}
function applyPattern() {
  const preset = $('input-situation').value;
  const editors = [...document.querySelectorAll('.source-editor')];
  const baseline = editors.map(editor => Number(currentCase.base.evidence.context_feature_values[`regime.source_${editor.dataset.source}`]));
  const center = baseline.reduce((a,b)=>a+b,0)/baseline.length;
  editors.forEach((editor,i) => {
    const value = preset === 'saved' ? baseline[i] : preset === 'agreement' ? center : preset === 'disagreement' ? center + (i-(editors.length-1)/2)*Math.max(1,Math.abs(center)*.8) : baseline[i]*1.8;
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
    const reason=!mask[i]?'Unavailable in this scenario, so its share is fixed at zero.':additive ? (top.length ? `Main score drivers versus the current mix: ${top.map(term=>`${featureLabel(term.key)} ${term.value>=0?'+':'−'}${num(Math.abs(term.value),2)}`).join('; ')}.`:'The learned source prior dominates this score.') : 'The network combines this site with three nearby contexts, then applies the availability mask.';
    return `<div class="weight-item"><div class="weight-top"><strong><span style="color:${colors[i%colors.length]}">●</span> ${profileLink(id)}</strong><b>${profileLink(id,pct(result.weights[i]))}</b></div><div class="weight-track"><div class="weight-fill" style="width:${Math.max(0,Math.min(100,result.weights[i]*100))}%;background:${colors[i%colors.length]}"></div></div><p>${esc(reason)}</p></div>`;
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
  const labels=['4 nearby sites',`${spec.spatial.state['project.weight'][0].length} basis features`,'32 hidden channels','4 attention heads','source weights'];
  const circles=coords.map((x,i)=>`<g><rect x="${x-72}" y="85" width="145" height="125" rx="15" fill="${i===4?'#e9fff8':'#f0efff'}" stroke="${i===4?'#60cdb6':'#afa5f4'}"/><text x="${x}" y="125" text-anchor="middle" font-size="13" font-weight="800" fill="#3c3c75">${labels[i]}</text><text x="${x}" y="154" text-anchor="middle" font-size="11" fill="#687590">${['spatial context','centered splines','learned projection','self-attention + FFN','masked softmax'][i]}</text></g>`).join('');
  const edges=coords.slice(0,-1).map((x,i)=>`<path d="M${x+73} 148 L${coords[i+1]-73} 148" stroke="#8a78e8" stroke-width="3" marker-end="url(#arrow)"/>`).join('');
  const output=spec.sources.map((id,i)=>`${sourceName(id)} ${pct(neural.weights[i])}`).join(' · ');
  $('network-svg').innerHTML=`<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#8a78e8"/></marker></defs>${edges}${circles}<text x="520" y="250" text-anchor="middle" font-size="12" fill="#65758c">${esc(output)}</text>`;
  $('network-method').innerHTML=networkInspector(spec,neural)+`<strong>Network calculation:</strong> exported PyTorch checkpoint, one encoder layer, four heads and ${spec.sources.length} source logits. The same edited context feeds both views. ${mask.filter(Boolean).length} source${mask.filter(Boolean).length===1?' is':'s are'} eligible. This neural path is a research challenger; the additive route is the saved accuracy champion.`;
}
function renderMap(spec) {
  const id=$('map-source').value || spec.sources[0];
  const cases=demo.cases.filter(item=>item.variable===currentCase.variable && item.location_id!==undefined).filter((item,i,all)=>all.findIndex(other=>other.location_id===item.location_id)===i);
  const x=lon=>115+(lon-72)*35, y=lat=>288-(lat-11)*14;
  const dots=cases.map(item=>{
    const source=item.base.sources.find(s=>s.source_id===id),weight=source?.weight||0,cx=x(item.base.target.longitude),cy=y(item.base.target.latitude);
    return `<g><circle cx="${cx}" cy="${cy}" r="${8+weight*22}" fill="#1c897c" fill-opacity="${.25+weight*.7}" stroke="#fff" stroke-width="2"/><text x="${cx}" y="${cy+37}" text-anchor="middle" font-size="12" font-weight="700" fill="#456c65">${esc(title(item.location_id))} ${pct(weight)}</text></g>`;
  }).join('');
  $('weight-map').innerHTML=`<path d="M90 285 Q160 115 405 56 Q630 74 790 250" fill="none" stroke="#b7d5c9" stroke-width="3" stroke-dasharray="7 7"/>${dots}`;
  $('map-range').textContent=`${sourceName(id)} across ${cases.length} saved locations. Prepared examples can have different dates and leads.`;
}
function renderLab() {
  if (!currentCase) return;
  try {
    const spec=gate.variables[currentCase.variable], values=formValues(spec), rows=scenarioRows(currentCase,values,spec.sources),mask=spec.sources.map(id=>values.sources[id].available);
    const additive=additiveForward(spec,rows[0],mask),neural=spatialForward(spec,rows,mask);
    const mixture=mixtureFromFixedSources(currentCase,spec.sources,additive.weights);
    window.anvayaContext={view:'research blend',scope:'Synthetic generated case, not live weather',site:currentCase.location_id,variable:currentCase.variable,weights:spec.sources.map((id,i)=>({source:id,weight:additive.weights[i],eligible:mask[i]})),lead_hours:values.lead};
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

const methodDescriptions={
  emos_adapted:['EMOS calibration','Fits a predictive distribution to ensemble output. The adapted implementation uses generated training cases to correct bias and dispersion. A lower score here is not a measured skill claim for India.'],
  bma_fixed_components:['Bayesian model averaging','Combines fixed calibrated component distributions with fitted mixture weights. These weights do not mean the probability that a source is correct.'],
  calibrated_equal:['Equal calibrated mixture','Gives the available calibrated sources equal shares. This is a simple reference against which adaptive weighting should be tested.'],
  inverse_skill:['Inverse-error weighting','Uses past evaluation error to set source shares. Performance depends on the training sample and whether that skill persists.'],
  xgboost_distributional_stack:['XGBoost distributional stack','A trained tree-based stacking method that combines source information into a predictive distribution on the generated benchmark.'],
  static:['Fixed learned weights','Fits one source-weight vector on the training data, without adapting it to the local forecast context.'],
  interpretable:['Explainable additive router','Adds learned context effects to each source score, applies an availability mask, then uses softmax to produce shares adding to 100%. Open Explain the blend for exact score contributions.'],
  spatial:['Spatial transformer','Uses the selected site and three neighbouring contexts to infer source scores through a trained attention network. Sensitivity tests explain output changes; attention alone is not a causal explanation.'],
  raw_equal_mixture:['Equal raw mixture','Combines uncalibrated source distributions with equal weights. It helps assess the value of calibration and adaptive routing.']
};
function methodName(id){if(methodDescriptions[id])return methodDescriptions[id][0];const [kind,source]=id.split(':');return `${({calibrated:'Calibrated',raw_members:'Raw ensemble',raw_mean:'Raw mean'})[kind]||title(kind)} · ${sourceName(source||id)}`;}
function openMethod(id){const source=id.split(':')[1],entry=methodDescriptions[id];$('model-detail').innerHTML=`<span class="eyebrow">BENCHMARK METHOD · GENERATED RESEARCH DATA</span><h1>${esc(methodName(id))}</h1><p class="method-description">${esc(entry?.[1]||`${id.startsWith('calibrated')?'Calibrated forecast distribution':id.startsWith('raw_members')?'Uncalibrated ensemble members':'Uncalibrated point mean'} for this source, evaluated on the same held-out generated cases.`)}</p>${source?`<p>Source model: ${profileLink(source)}. Open its profile for architecture, uses, limitations and primary references.</p>`:''}<p class="fineprint">Benchmark scores measure errors, not model weights. Lower scores are better only within the displayed test. The training examples are synthetic, and these results do not validate an operational Indian forecast.</p><a class="model-link" href="#maps/neural" id="method-lab-link">Explore weights and exact calculations →</a>`;$('model-dialog').showModal();$('method-lab-link').addEventListener('click',()=>$('model-dialog').close());}

function modelFamily(item) { return item.ensemble?'ensemble':item.model_class==='nwp'?'nwp':item.model_class==='ai'?'ai':item.model_class==='statistical'?'statistical':'hybrid'; }
function atlasMetadata(item) {
  const live=['gfs','ifs_control','aifs_single','icon_global'].includes(item.id),ensemble=item.id==='gefs';
  const documented={gfs:['0.11 degrees (about 13 km) surface fields; delivery grids vary','16 days','Every 6 hours','https://open-meteo.com/en/docs/gfs-api'],ifs_control:['0.25-degree open-data delivery (about 25 km); distinct from 9 km HRES','15 days','Every 6 hours','https://open-meteo.com/en/docs/ecmwf-api'],aifs_single:['0.25-degree delivery (about 28 km)','15 days','Every 6 hours','https://open-meteo.com/en/docs/ecmwf-api'],gefs:['0.25 / 0.5-degree seamless ensemble; 31 members','10 days at 0.25 degrees, 35 days at 0.5 degrees','Every 6 hours','https://open-meteo.com/en/docs/ensemble-api']}[item.id];
  const fields=[['Resolution',item.grid||'Not verified for this integration'],['Forecast range',live?'96-hour window fetched here; native range varies':ensemble?'48-hour window fetched for guidance':'Not verified for this integration'],['Update frequency','Provider dependent; see primary documentation'],['Variables',Object.entries(item.capabilities||{}).filter(([,v])=>v===true).map(([k])=>k).join(', ')||'Not documented'],['Coverage',live||ensemble?'Global provider grid; India interface':item.use_context?.region||'See model documentation'],['Data access',live?'Named forecast API':ensemble?'Ensemble API':item.access||'Candidate; no active feed'],['Licence',item.data_license||'Verify with provider before ingestion']];
  if(documented){fields[0][1]=documented[0];fields[1][1]=`${documented[1]}; this app fetches ${ensemble?48:96} hours`;fields[2][1]=documented[2];}
  return `<details class="atlas-metadata"><summary>Dataset & operational metadata</summary><dl>${fields.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>${documented?`<a href="${documented[3]}" target="_blank" rel="noopener noreferrer">Provider specifications · reviewed 28 Sep 2026</a>`:''}</details>`;
}
function renderAtlas() {
  if (!catalogue) return;
  const query=$('model-search').value.trim().toLowerCase(),family=$('model-family').value,variable=$('model-variable').value;
  const filtered=catalogue.models.filter(item=>(family==='all'||modelFamily(item)===family)&&(variable==='all'||item.capabilities?.[variable]===true)&&(!query||[item.name,item.provider,item.description,...Object.values(item.use_context||{})].join(' ').toLowerCase().includes(query)));
  $('model-count').textContent=`${filtered.length} / ${catalogue.models.length} profiles`;
  $('model-grid').innerHTML=filtered.map(item=>`<article class="model-card"><div class="model-meta"><span>${esc(modelFamily(item))}</span><span>${esc(item.provider||'Research')}</span><span class="status">${['gfs','ifs_control','aifs_single','icon_global'].includes(item.id)?'Raw feed shown here':item.catalogue_only?'New candidate profile':'Candidate profile'}</span></div><h2>${esc(item.name)}</h2><p>${esc(item.description||item.purpose)}</p>${atlasMetadata(item)}<div class="context-grid">${[['Region','region'],['Season','season'],['Lead time','lead'],['Weather situation','weather']].map(([label,key])=>`<div class="context-cell"><strong>${label}</strong><span>${esc(item.use_context?.[key]||'Context requires independent validation.')}</span></div>`).join('')}</div><button type="button" data-model="${esc(item.id)}">View full analysis ↗</button></article>`).join('')||'<p>No models match these filters.</p>';
  $('model-grid').querySelectorAll('[data-model]').forEach(button=>button.addEventListener('click',()=>openModel(button.dataset.model)));
}
async function openModel(id) {
  catalogue ||= await loadResearch('catalogue');
  const item=catalogue.models.find(model=>model.id===id);if(!item)return;
  window.anvayaContext={view:'model profile',model_id:id,model_name:item.name};
  const context=item.use_context||{};
  const links=(item.evidence_urls||[]).filter(url=>/^https:\/\//.test(url)).slice(0,5);
  const participation=['gfs','ifs_control','aifs_single'].includes(id)?'A raw provider series is shown in the live desk. This model has not been fitted into a real-data Anvaya X blend.':'This is a documented candidate. It is not fetched by the live desk or assigned an operational blend weight.';
  $('model-detail').innerHTML=`<span class="eyebrow">MODEL ANALYSIS · ${esc(item.provider||'Research')}</span><h1>${esc(item.name)}</h1><p>${esc(item.description||item.purpose)}</p><div class="model-meta"><span>${esc(modelFamily(item))}</span><span>${esc(item.architecture||'Architecture documented in source')}</span></div><div class="detail-box" style="margin-top:18px"><strong>Actual participation in Anvaya X</strong>${esc(participation)}</div><h2 style="margin-top:25px">When should it be considered?</h2><div class="detail-grid">${[['Region',context.region],['Season',context.season],['Lead time',context.lead],['Weather situation',context.weather]].map(([label,text])=>`<div class="detail-box"><strong>${label}</strong>${esc(text||'Requires local validation.')}</div>`).join('')}</div><h2 style="margin-top:25px">Deeper analysis</h2><p>${esc(context.analysis||item.purpose)}</p><div class="detail-grid"><div class="detail-box"><strong>Strengths</strong><ul class="detail-list">${(item.pros||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></div><div class="detail-box"><strong>Limits and conditions</strong><ul class="detail-list">${(item.cons||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul></div></div><p class="fineprint">${esc(item.assessment_basis||'Suitability profile, not a measured local skill ranking.')}</p>${links.length?`<h3>Primary references</h3><ul class="detail-list">${links.map((url,i)=>`<li><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Provider / author source ${i+1} ↗</a></li>`).join('')}</ul>`:''}`;
  if(!$('model-dialog').open)$('model-dialog').showModal();
}

let forecastSerial=0, forecastController;
function setLiveSelection(selection) {
  liveSelection=selection; ++locationSearchSerial;
  $('chosen-city').textContent=selection.label;
  $('refresh-status').textContent='Forecasts for your selected location ? refresh every 15 minutes';
  $('public-selected').textContent=`Selected: ${selection.label}`;
  $('selection-note').textContent=selection.note||'';
  $('public-search').value='';$('location-results').hidden=true;$('public-search').setAttribute('aria-expanded','false');
  $('public-content').hidden=true;$('download-forecast').disabled=true;loadPublic();
}
let locationSearchTimer, locationSearchSerial=0;
async function searchPlaces() {
  const query=$('public-search').value.trim(), serial=++locationSearchSerial, list=$('location-results');
  if(query.length<2){list.hidden=true;$('public-search').setAttribute('aria-expanded','false');return;}
  list.hidden=false;$('public-search').setAttribute('aria-expanded','true');list.innerHTML='<div class="location-empty">Searching Indian places…</div>';
  try {
    const data=await readJSON(await fetch(`/api/locations?q=${encodeURIComponent(query)}`,{signal:AbortSignal.timeout(35000)}));
    if(serial!==locationSearchSerial)return;
    list.innerHTML=(data.message?`<div class="location-empty">${esc(data.message)}</div>`:'')+(data.locations.length?data.locations.map((place,i)=>`<button class="location-result" role="option" type="button" data-place-id="${i}"><strong>${esc(place.name)}${place.pincode?` · PIN ${esc(place.pincode)}`:''}</strong><small>${esc([place.district,place.state].filter(Boolean).join(', '))} · ${num(place.latitude,3)}°, ${num(place.longitude,3)}°${place.precision==='district approximation'?' · Approximate district centre':''}</small></button>`).join(''):'<div class="location-empty">No match found. Try a district name, or use your location / coordinates.</div>');
    list.querySelectorAll('[data-place-id]').forEach(button=>button.addEventListener('click',()=>{const place=data.locations[Number(button.dataset.placeId)];setLiveSelection({key:`place:${place.id}`,parameter:`place_id=${place.id}`,label:[place.name,place.state,place.pincode?`PIN ${place.pincode}`:''].filter(Boolean).join(', '),note:place.note,place_id:place.id});}));
  }catch(error){if(serial===locationSearchSerial)list.innerHTML=`<div class="location-empty">${esc(error.message)}</div>`;}
}
async function loadPublic(force=false) {
  if(!liveSelection){$('public-content').hidden=true;$('download-forecast').disabled=true;$('refresh-forecast').disabled=true;setStatus('public-message','Choose your city above to see its forecast. Search a place, select a quick pick, or use your location.');return;}
  const selection=liveSelection, serial=++forecastSerial, cached=liveCache.get(selection.key);
  if(!force && cached && Date.parse(cached.data.expires_at)>Date.now()){forecastController?.abort();$('refresh-forecast').disabled=false;renderPublic(cached.data);return;}
  forecastController?.abort();forecastController=new AbortController();
  setStatus('public-message',`Retrieving source forecasts for ${selection.label}…`);$('refresh-forecast').disabled=true;
  try {
    const data=await readJSON(await fetch(`/api/public-forecast?${selection.parameter}`,{signal:AbortSignal.any([forecastController.signal,AbortSignal.timeout(40000)])}));
    if(data.data_kind!=='real'||!Array.isArray(data.rows))throw Error('The forecast service did not return valid data.');
    liveCache.set(selection.key,{data,at:Date.now()});if(liveCache.size>20)liveCache.delete(liveCache.keys().next().value);
    if(serial===forecastSerial)renderPublic(data);
  }catch(error){
    if(serial!==forecastSerial||error.name==='AbortError')return;
    if(cached){renderPublic(cached.data);setStatus('public-message',`Refresh failed. Showing saved data retrieved ${formatIST(cached.data.retrieved_at)}. ${error.message}`,true);}
    else{$('public-content').hidden=true;setStatus('public-message',error.message,true);}
  }finally{if(serial===forecastSerial)$('refresh-forecast').disabled=false;}
}
function renderOverview(data) {
  const overview=data.overview;
  if(!overview){$('current-overview').innerHTML='<div class="forecast-empty">The daily overview is temporarily unavailable. Named hourly forecasts are available below.</div>';return;}
  const c=overview.current,d=overview.daily;
  const value=(v,u)=>Number.isFinite(v)?`${num(v)}${u}`:'—';
  $('current-overview').innerHTML=`<article class="card current-weather"><span class="eyebrow">CURRENT MODEL ESTIMATE</span><h2>${esc(weatherDescription(c.weather_code))}</h2><div class="current-temp">${value(c.temperature_2m,'°')}</div><div class="current-meta"><span>Feels like ${value(c.apparent_temperature,'°C')}</span><span>Humidity ${value(c.relative_humidity_2m,'%')}</span><span>Wind ${value(c.wind_speed_10m,' m/s')}</span></div><p>Valid ${esc(formatIST(`${c.time}+05:30`))} · automatic model selection. Model estimate, not a station reading.</p></article><article class="card"><span class="eyebrow">THE WEEK AHEAD · IST</span><h2>Seven days at a glance</h2><div class="daily-strip">${d.time.map((day,i)=>`<div class="day-card"><span>${i===0?'Today':new Intl.DateTimeFormat('en-IN',{weekday:'short',timeZone:'Asia/Kolkata'}).format(new Date(day+'T12:00:00+05:30'))}<br>${esc(day.slice(5))}</span><strong>${value(d.temperature_2m_max?.[i],'°')} <small>/ ${value(d.temperature_2m_min?.[i],'°')}</small></strong><span class="rain-value">${value(d.precipitation_sum?.[i],' mm')}</span><small>${value(d.precipitation_probability_max?.[i],'%')} rain chance</small></div>`).join('')}</div><p class="daily-note">Daily high / low, rain total and maximum hourly precipitation probability from the provider's automatic model selection. Separate from the named model comparison below.</p></article>`;
}
function loadHero() {
  $('hero-live').innerHTML='<span class="live-dot">WEATHER INTELLIGENCE · INDIA</span><strong>Many models. One clearer picture.</strong><small>Physical forecasts, AI models and inspectable blending for any selected location.</small>';
}

function renderPublic(data) {
  const variable = $('public-variable').value, horizon = Number($('public-horizon').value);
  $('variable-help').textContent=weatherHelp[variable];
  renderOverview(data);
  $('download-forecast').disabled=false;
  $('refresh-status').textContent=`Auto-refresh: 15 min · retrieved ${formatIST(data.retrieved_at)}`;
  $('source-health').innerHTML=(data.source_status||[]).map(source=>`<span class="source-badge ${source.available?'':'unavailable'}">${source.available?'●':'○'} ${esc(source.name)} · ${source.available?'available':'temporarily unavailable'}</span>`).join('');
  window.anvayaContext={view:'live forecasts',selection:liveSelection.label,parameters:liveSelection.parameter,coordinates:data.requested_coordinates,location_note:liveSelection.note||'',variable,horizon};
  const ids = Object.keys(data.models), times = futureTargetTimes(data.rows,Date.now(),horizon);
  const windows = ids.map(id=>modelWindow(data.rows,id,variable,times));
  const all = windows.flatMap(window=>window.points.map(point=>point.value)).filter(Number.isFinite);
  if (!times.length || !all.length) {
    $('public-content').hidden = true;
    setStatus('public-message','The provider returned no future values for this target. Retry shortly.',true);
    return;
  }
  const min = Math.min(...all), max = Math.max(...all), range = Math.max(1,max-min), unit = data.units[variable];
  $('threshold-unit').textContent = unit;
  const thresholdText = $('public-threshold').value.trim();
  const threshold = thresholdText !== '' && Number.isFinite(Number(thresholdText)) ? Number(thresholdText) : null;
  $('public-content').hidden = false;
  setStatus('public-message',`${Date.parse(data.expires_at)<Date.now()?'Saved data — refresh overdue. ':''}Source attribution and documentation are available below.`);
  $('public-stats').innerHTML = stat('Selected place',data.city,data.requested_location?.state || 'India')
    + stat('Future target hours',times.length,`${formatIST(times[0])} onward`)
    + stat('Model value range',`${num(min)}–${num(max)} ${unit}`,'Across the displayed source forecasts')
    + stat('Retrieved at',formatIST(data.retrieved_at),'Provider retrieval; model cycles not pinned');
  $('public-chart-title').textContent = `${weatherLabels[variable]} · ${data.city}`;
  $('public-legend').innerHTML = ids.map((id,i)=>`<span><i style="background:${colors[i]}"></i>${esc(data.models[id].name)}</span>`).join('');
  const left = 47, right = 865, top = 32, bottom = 258;
  const guides = [0,.25,.5,.75,1].map(frac=>`<line x1="${left}" x2="${right}" y1="${bottom-frac*(bottom-top)}" y2="${bottom-frac*(bottom-top)}" stroke="#e5ece7"/><text x="39" y="${bottom-frac*(bottom-top)+4}" text-anchor="end" font-size="11" fill="#78918b">${num(min+frac*range,1)}</text>`).join('');
  const xTicks = [0,.25,.5,.75,1].map(frac=>{
    const i = Math.round(frac*(times.length-1));
    return `<text x="${left+frac*(right-left)}" y="291" text-anchor="middle" font-size="10" fill="#78918b">${esc(formatIST(times[i],{date:false}))}</text>`;
  }).join('');
  const paths = windows.map((window,j)=>{
    let drawing = false;
    const path = window.points.map((point,i)=>{
      if (!Number.isFinite(point.value)) { drawing=false; return ''; }
      const next = `${drawing?'L':'M'}${(left+i*(right-left)/Math.max(times.length-1,1)).toFixed(1)} ${(bottom-(point.value-min)/range*(bottom-top)).toFixed(1)}`;
      drawing=true; return next;
    }).join(' ');
    return `<path d="${path}" fill="none" stroke="${colors[j]}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join('');
  const thresholdLine = threshold !== null && threshold >= min && threshold <= max
    ? `<line x1="${left}" x2="${right}" y1="${bottom-(threshold-min)/range*(bottom-top)}" y2="${bottom-(threshold-min)/range*(bottom-top)}" stroke="#ad6947" stroke-width="2" stroke-dasharray="6 5"/><text x="${right}" y="${bottom-(threshold-min)/range*(bottom-top)-5}" text-anchor="end" font-size="10" fill="#ad6947">Your threshold ${num(threshold,1)} ${esc(unit)}</text>` : '';
  $('public-chart').innerHTML = guides + xTicks + thresholdLine + paths + windows.map((window,j)=>window.points.map((point,i)=>Number.isFinite(point.value)?`<circle cx="${left+i*(right-left)/Math.max(times.length-1,1)}" cy="${bottom-(point.value-min)/range*(bottom-top)}" r="6" fill="transparent"><title>${esc(data.models[ids[j]].name)} · ${esc(formatIST(point.time))}: ${num(point.value,1)} ${esc(unit)}</title></circle>`:'').join('')).join('');
  renderLiveAnalytics(data,windows,times,unit);
  $('public-chart-caption').textContent = `${formatIST(times[0])} to ${formatIST(times.at(-1))} · ${unit} · separate source forecasts, not a blend`;
  $('public-model-cards').innerHTML = ids.map((id,i)=>{
    const window=windows[i];
    const primary=!window.count?'Unavailable':variable==='precipitation' ? `${num(window.sum,1)} mm` : `${num(window.max,1)} ${unit}`;
    const label=variable==='precipitation'?(window.count===times.length?'Total for selected window':'Sum of available hours'):'Highest value in selected window';
    const detail=window.count ? `Peak hour ${num(window.max,1)} ${unit} at ${formatIST(window.maxTime)} · ${window.count} available hours` : 'No usable values in this window';
    const crossings=threshold===null?'':`<small>${window.points.filter(point=>Number.isFinite(point.value)&&point.value>threshold).length} forecast hour(s) above your ${num(threshold,1)} ${esc(unit)} threshold.</small>`;
    return `<div class="model-outlook"><strong><span style="color:${colors[i]}">●</span> ${profileLink(id,data.models[id].name)}</strong><b>${esc(primary)}</b><small>${label}. ${esc(detail)}</small>${crossings}</div>`;
  }).join('');
  $('public-table').querySelector('thead').innerHTML = `<tr><th>${variable==='precipitation'?'Hour ending (IST)':'Valid time (IST)'}</th>${ids.map(id=>`<th>${esc(data.models[id].name)} (${esc(unit)})</th>`).join('')}</tr>`;
  $('public-table').querySelector('tbody').innerHTML = times.map((time,i)=>`<tr><td>${esc(formatIST(time))}</td>${windows.map(window=>`<td>${window.points[i].value===null?'—':num(window.points[i].value,1)}</td>`).join('')}</tr>`).join('');
  const receipt = [
    ['Provider','See data attribution in footer'],['Requested place',[data.city,data.requested_location?.state].filter(Boolean).join(', ')],
    ['Retrieved',formatIST(data.retrieved_at)],['First target',formatIST(times[0])],['Last target',formatIST(times.at(-1))],
    ['Requested coordinates',`${num(data.requested_coordinates.latitude,4)}, ${num(data.requested_coordinates.longitude,4)}`],
    ['Provider grid',`${num(data.returned_coordinates.latitude,4)}, ${num(data.returned_coordinates.longitude,4)}`],
    ['Model cycles','Not pinned by latest-series endpoint'],['SHA-256 receipt',data.sha256],
  ];
  $('public-receipt').innerHTML = receipt.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
}

async function start() {
  setupPlatform(()=>liveSelection);
  setupChat();loadHero();
  document.addEventListener('click',async event=>{const profile=event.target.closest('[data-profile]');if(profile){event.preventDefault();try{await openModel(profile.dataset.profile);}catch{$('model-detail').textContent='The model profile could not be loaded.';$('model-dialog').showModal();}}const method=event.target.closest('[data-method]');if(method)openMethod(method.dataset.method);});
  $('refresh-forecast').addEventListener('click',()=>loadPublic(true));
  $('download-forecast').addEventListener('click',()=>{const data=liveCache.get(liveSelection?.key)?.data;if(data)downloadForecast(data);});
  const selectCoordinates=(latitude,longitude)=>setLiveSelection({key:`coords:${latitude}:${longitude}`,parameter:new URLSearchParams({latitude,longitude}).toString(),label:`${Number(latitude).toFixed(4)}° N, ${Number(longitude).toFixed(4)}° E`,note:'Forecast for your supplied coordinates; the provider uses a nearby model grid cell.'});
  $('coordinate-form').addEventListener('submit',event=>{event.preventDefault();selectCoordinates($('latitude').value,$('longitude').value);});
  $('use-location').addEventListener('click',()=>{if(!navigator.geolocation){setStatus('public-message','Your browser does not support location. Enter coordinates instead.',true);return;}setStatus('public-message','Waiting for your location permission…');navigator.geolocation.getCurrentPosition(position=>selectCoordinates(position.coords.latitude,position.coords.longitude),()=>setStatus('public-message','Location was unavailable or permission was declined. Search your town or enter coordinates.',true),{timeout:12000,maximumAge:300000});});
  setInterval(()=>{if(!document.hidden && location.hash==='#public')loadPublic();},60000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && location.hash==='#public')loadPublic();});
  document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>{location.hash=button.dataset.go;}));
  window.addEventListener('hashchange',()=>nav(location.hash.slice(1)));
  $('lab-variable').addEventListener('change',chooseLocation);
  $('lab-location').addEventListener('change',chooseCase);
  $('lab-case').addEventListener('change',resetCase);
  ['input-lead','input-date','input-coast','input-elevation','map-source'].forEach(id=>$(id).addEventListener('input',renderLab));
  $('input-situation').addEventListener('change',applyPattern);
  $('reset-scenario').addEventListener('click',resetCase);
  document.querySelectorAll('[data-lab-tab]').forEach(button=>button.addEventListener('click',()=>{
    labTab=button.dataset.labTab;
    $('panel-weights').hidden=labTab!=='weights';$('panel-network').hidden=labTab!=='network';
    document.querySelectorAll('[data-lab-tab]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));
  }));
  $('model-search').addEventListener('input',renderAtlas);
  ['model-family','model-variable'].forEach(id=>$(id).addEventListener('change',renderAtlas));
  ['public-variable','public-horizon'].forEach(id=>$(id).addEventListener('change',()=>{
    if(id==='public-variable')$('public-threshold').value='';
    const cached=liveCache.get(liveSelection?.key);
    if(cached)renderPublic(cached.data); else loadPublic();
  }));
  $('public-threshold').addEventListener('input',()=>{
    const cached=liveCache.get(liveSelection?.key);
    if(cached)renderPublic(cached.data);
  });
  document.querySelectorAll('[data-city]').forEach(button=>button.addEventListener('click',()=>setLiveSelection({
    key:`city:${button.dataset.city}`,parameter:`city=${encodeURIComponent(button.dataset.city)}`,
    label:button.dataset.city,
  })));
  $('public-search').addEventListener('input',()=>{++locationSearchSerial;clearTimeout(locationSearchTimer);locationSearchTimer=setTimeout(searchPlaces,300);});
  $('public-search').addEventListener('keydown',event=>{
    if(event.key==='Escape'){$('location-results').hidden=true;$('public-search').setAttribute('aria-expanded','false');}
    if(event.key==='Enter')$('location-results').querySelector('[data-place-id]')?.click();
  });
  $('close-model').addEventListener('click',()=>$('model-dialog').close());
  $('model-dialog').addEventListener('click',event=>{if(event.target===$('model-dialog'))$('model-dialog').close();});
  nav(location.hash.slice(1));
}
start();
