const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const fmt = (value, digits = 1) => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits);
const pct = value => value === null || value === undefined ? '—' : `${(100 * value).toFixed(value < .001 ? 3 : 1)}%`;
const colors = { gfs_global: '#238dd0', ecmwf_ifs025: '#0cab98', ecmwf_aifs025_single: '#e59b3a', gefs: '#288bc0', gfs: '#0cab98', graphcast_operational: '#db9d40' };
const names = { gfs_global: 'GFS', ecmwf_ifs025: 'IFS', ecmwf_aifs025_single: 'AIFS', gefs: 'GEFS', gfs: 'GFS', graphcast_operational: 'GraphCast' };
const vars = { temperature_2m: ['Temperature', '°C'], precipitation: ['Precipitation', 'mm/h'], wind_speed_10m: ['Wind speed', 'm/s'] };
const titleCase = value => String(value).replace(/_/g, ' ').replace(/\b\w/g, x => x.toUpperCase());
let demo, benchmark, catalogue, live;

function navigate(view) {
  if (!['overview', 'public', 'lab', 'evidence', 'atlas'].includes(view)) view = 'overview';
  $$('.view').forEach(node => node.hidden = node.id !== `view-${view}`);
  $$('#nav a').forEach(node => node.classList.toggle('active', node.dataset.view === view));
  $('#page-title').textContent = titleCase(view);
  if (view === 'public' && !live) loadPublic();
  if (view === 'lab') renderLab();
  if (view === 'evidence') renderEvidence();
  if (view === 'atlas') renderAtlas();
  window.scrollTo({ top: 0, behavior: 'instant' });
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function stats(items) {
  return items.map(([label, value, meta]) => `<div class="stat"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value)}</div><div class="stat-meta">${escapeHtml(meta)}</div></div>`).join('');
}

async function loadPublic() {
  const city = $('#public-city').value;
  const message = $('#public-message');
  message.className = '';
  message.textContent = `Fetching named model forecasts for ${city}…`;
  $('#public-content').hidden = true;
  try {
    live = await getJson(`/api/public-forecast?city=${encodeURIComponent(city)}`);
    if (live.data_kind !== 'real' || live.trained_blend_applied !== false) throw new Error('Unexpected provenance payload');
    message.textContent = '';
    renderPublic();
  } catch (error) {
    live = null;
    message.className = 'error';
    message.textContent = `Live forecast unavailable: ${error.message}. Please try again shortly.`;
  }
}

function renderPublic() {
  if (!live) return;
  const variable = $('#public-variable').value;
  const [label, unit] = vars[variable];
  const modelIds = Object.keys(live.models);
  const byModel = Object.fromEntries(modelIds.map(id => [id, live.rows.filter(row => row.model === id)]));
  const now = Date.now();
  const next = modelIds.map(id => byModel[id].find(row => Date.parse(row.valid_time) >= now && row[variable] !== null) || byModel[id].find(row => row[variable] !== null));
  $('#public-stats').innerHTML = stats([
    ...modelIds.map((id, index) => [live.models[id].name, next[index] ? `${fmt(next[index][variable])} ${unit}` : '—', next[index] ? new Date(next[index].valid_time).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC' : 'No value']),
    ['Model range', next.every(Boolean) ? `${fmt(Math.max(...next.map(row => row[variable])) - Math.min(...next.map(row => row[variable])))} ${unit}` : '—', 'Difference across available models; not uncertainty']
  ]);
  $('#public-chart-title').textContent = `${label} · next 72 hours`;
  $('#public-legend').innerHTML = modelIds.map(id => `<span><i style="background:${colors[id]}"></i>${escapeHtml(live.models[id].name)}</span>`).join('');
  drawChart(byModel, variable, unit);
  const times = byModel[modelIds[0]].filter(row => Date.parse(row.valid_time) >= now).slice(0, 8).map(row => row.valid_time);
  $('#public-table thead').innerHTML = `<tr><th>Valid UTC</th>${modelIds.map(id => `<th>${escapeHtml(names[id])}</th>`).join('')}</tr>`;
  $('#public-table tbody').innerHTML = times.map(time => `<tr><td>${escapeHtml(new Date(time).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }))}</td>${modelIds.map(id => `<td>${fmt(byModel[id].find(row => row.valid_time === time)?.[variable])}</td>`).join('')}</tr>`).join('');
  const receipt = [['Retrieved', live.retrieved_at], ['Provider', 'Open-Meteo'], ['City', live.city], ['SHA-256', live.sha256], ['Run cycle', 'Unpinned'], ['Blend status', 'Not applied']];
  $('#public-receipt').innerHTML = receipt.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join('') + `<dt>Source</dt><dd><a href="${escapeHtml(live.source_url)}" target="_blank" rel="noopener noreferrer">Open provider request ↗</a></dd>`;
  $('#public-content').hidden = false;
}

function drawChart(byModel, variable, unit) {
  const ids = Object.keys(byModel);
  const rows = byModel[ids[0]];
  const validTimes = rows.map(row => Date.parse(row.valid_time));
  const minTime = Math.min(...validTimes), maxTime = Math.max(...validTimes);
  const values = ids.flatMap(id => byModel[id].map(row => row[variable]).filter(x => x !== null));
  if (!values.length) { $('#public-chart').innerHTML = ''; return; }
  let min = Math.min(...values), max = Math.max(...values);
  if (max === min) { max += 1; min -= 1; }
  const margin = (max - min) * .12;
  min -= margin; max += margin;
  const sx = time => 48 + (Date.parse(time) - minTime) / (maxTime - minTime || 1) * 827;
  const sy = val => 255 - (val - min) / (max - min) * 220;
  const grid = Array.from({ length: 5 }, (_, i) => { const y = 35 + i * 55; const v = max - (y - 35) / 220 * (max - min); return `<line x1="48" y1="${y}" x2="875" y2="${y}" stroke="#e8eeee"/><text x="3" y="${y + 4}" fill="#8da2a8" font-size="11">${fmt(v)}</text>`; }).join('');
  const paths = ids.map(id => {
    const segments = []; let current = [];
    for (const row of byModel[id]) { if (row[variable] === null) { if (current.length) segments.push(current); current = []; } else current.push(`${sx(row.valid_time).toFixed(1)},${sy(row[variable]).toFixed(1)}`); }
    if (current.length) segments.push(current);
    return segments.map(segment => `<polyline points="${segment.join(' ')}" fill="none" stroke="${colors[id]}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  }).join('');
  $('#public-chart').innerHTML = grid + paths;
  $('#public-chart-caption').innerHTML = `<span>${escapeHtml(new Date(minTime).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }))} UTC</span><span>${escapeHtml(labelFor(variable))} (${escapeHtml(unit)})</span><span>${escapeHtml(new Date(maxTime).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }))} UTC</span>`;
}
const labelFor = variable => vars[variable][0];

function selectedCase() {
  if (!demo) return null;
  const variable = $('#lab-variable').value;
  const location = $('#lab-location').value;
  const selected = demo.cases[Number($('#lab-case').value)];
  return selected?.variable === variable && selected?.location_id === location ? selected : null;
}
function syncLabLocations() {
  if (!demo) return;
  const variable = $('#lab-variable').value;
  const select = $('#lab-location');
  const previous = select.value;
  const locations = [...new Set(demo.cases.filter(item => item.variable === variable && item.selection.startsWith('latest')).map(item => item.location_id))];
  select.innerHTML = locations.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(titleCase(id))}</option>`).join('');
  select.value = locations.includes(previous) ? previous : locations.includes('mumbai') ? 'mumbai' : locations[0];
  syncLabCases();
}
function syncLabCases() {
  const variable = $('#lab-variable').value;
  const location = $('#lab-location').value;
  const examples = demo.cases.map((item, index) => ({ ...item, index })).filter(item => item.variable === variable && item.location_id === location);
  $('#lab-case').innerHTML = examples.map(item => `<option value="${item.index}">${item.selection.startsWith('illustrative') ? 'Illustrative high-rain case' : 'Latest prepared case'}</option>`).join('');
  syncLabOutages();
}
function syncLabOutages() {
  const selected = selectedCase();
  const select = $('#lab-outage');
  select.innerHTML = '<option value="">All eligible sources</option>' + (selected?.base.sources || []).map(source => `<option value="${escapeHtml(source.source_id)}">Remove ${escapeHtml(names[source.source_id] || titleCase(source.source_id))}</option>`).join('');
  renderLab();
}
function renderLab() {
  const selected = selectedCase();
  if (!selected) { $('#lab-message').textContent = 'Lab data is unavailable.'; return; }
  const excluded = $('#lab-outage').value;
  const receipt = excluded ? selected.source_outages[excluded] : selected.base;
  if (!receipt) return;
  $('#lab-message').textContent = excluded ? `Counterfactual: ${names[excluded] || excluded} removed and eligible sources reweighted.` : '';
  const summary = receipt.summary || {};
  const unit = summary.units === 'degC' ? '°C' : summary.units || '';
  const point = Array.isArray(summary.mean) ? summary.speed_mean : summary.mean;
  const label = selected.variable === 'wind_uv' ? 'Mean wind speed' : 'Mean forecast';
  const probabilities = receipt.probabilities || [];
  $('#lab-stats').innerHTML = stats([
    [label, `${fmt(point)} ${unit}`, selected.variable === 'wind_uv' ? 'Derived from joint u/v distribution' : 'Calibrated predictive distribution'],
    ['10–90% range', `${fmt(summary.quantiles?.['0.1'])}–${fmt(summary.quantiles?.['0.9'])} ${unit}`, 'Marginal range, synthetic case'],
    ['Eligible experts', String(receipt.sources?.length || 0), `${receipt.eligibility?.filter(x => x.status === 'excluded').length || 0} candidates excluded`],
    ['Valid period', new Date(receipt.target.valid_start).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }), `${new Date(receipt.target.valid_start).toLocaleTimeString('en-IN', { timeStyle: 'short', timeZone: 'UTC' })} UTC start`]
  ]);
  const sourceRows = receipt.sources || [];
  $('#weight-bars').innerHTML = sourceRows.length ? sourceRows.map((source, index) => `<div class="weight-row"><div class="weight-head"><span>${escapeHtml(names[source.source_id] || titleCase(source.source_id))} <small>· calibrated ${fmt(source.calibrated_mean)} ${escapeHtml(unit)}</small></span><strong>${pct(source.weight)}</strong></div><div class="weight-track"><div class="weight-fill" style="width:${Math.max(0, Math.min(100, source.weight * 100))}%;background:${colors[source.source_id] || ['#178f98','#5189c7','#de9a54'][index % 3]}"></div></div></div>`).join('') : '<div class="empty">No eligible experts.</div>';
  $('#lab-events').innerHTML = probabilities.length ? probabilities.map(item => `<div class="event"><div class="event-top"><span>Rainfall ≥ ${fmt(item.threshold)} mm / 24 h</span><strong>${pct(item.probability)}</strong></div><div class="event-line"><span style="width:${Math.max(.5, Math.min(100, item.probability * 100))}%"></span></div><small>${Object.entries(item.contributions || {}).map(([id, amount]) => `${escapeHtml(names[id] || id)} ${pct(amount)}`).join(' · ')}</small></div>`).join('') : '<p class="fineprint">This prepared case has no threshold exceedance probabilities. Inspect its calibrated distribution and source weights.</p>';
  const termTotals = {};
  for (const source of sourceRows) for (const [term, amount] of Object.entries(source.term_contributions || {})) termTotals[term] = (termTotals[term] || 0) + Math.abs(amount);
  const topTerms = Object.entries(termTotals).filter(([term]) => term !== 'intercept').sort((a, b) => b[1] - a[1]).slice(0, 7);
  $('#lab-terms').innerHTML = topTerms.map(([term]) => `<div class="terms-row"><span>${escapeHtml(term.replaceAll('.', ' · '))}</span><strong>${sourceRows.map(source => { const x = source.term_contributions?.[term] || 0; return `<span class="${x >= 0 ? 'term-pos' : 'term-neg'}">${x >= 0 ? '+' : ''}${fmt(x, 3)}</span>`; }).join(' / ')}</strong></div>`).join('') + '<p class="fineprint">Each column follows the source order in the weight panel; values are logit units.</p>';
  const active = (receipt.eligibility || []).filter(x => x.status === 'active');
  const inactive = (receipt.eligibility || []).filter(x => x.status !== 'active');
  $('#lab-ledger').innerHTML = [...active, ...inactive].slice(0, 8).map(item => `<div class="ledger-item ${item.status === 'active' ? 'active' : ''}"><strong>${escapeHtml(item.name || item.source_id)}<span>${escapeHtml(item.status)}</span></strong><small>${escapeHtml((item.reasons || []).join(' '))}</small></div>`).join('') + `<p class="fineprint">${inactive.length} excluded candidates in total. Only the first exclusions are shown here.</p>`;
  $('#lab-receipt').textContent = `Receipt ${receipt.receipt_id} · model digest ${receipt.model_digest} · ${selected.selection}.`;
  drawWeightMap(selected.variable, excluded, receipt.sources || []);
  $('#lab-content').hidden = false;
}

function drawWeightMap(variable, excluded, sources) {
  let panel = $('#weight-map-panel');
  if (!panel) {
    panel = document.createElement('article');
    panel.className = 'card weight-map-panel';
    panel.id = 'weight-map-panel';
    panel.innerHTML = '<div class="card-heading"><div><div class="eyebrow ink">GEOGRAPHIC EXAMPLE</div><h2>Source weight by prepared location</h2></div><label class="map-select-label">Source<select id="map-source"></select></label></div><div class="map-layout"><svg id="weight-map" role="img" aria-label="Six site map of model weights" viewBox="0 0 900 350" preserveAspectRatio="xMidYMid meet"></svg><div class="map-note"><strong>Six-site synthetic map</strong><p>Each marker is the routing weight at one prepared location for the same variable and source scenario. Marker area and opacity indicate mixture share. This is not an interpolated India skill map.</p><div id="map-range"></div></div></div>';
    const lowerCards = $$('#lab-content > .two-up.below');
    if (lowerCards[1]) lowerCards[1].before(panel);
    else $('#lab-content').append(panel);
    panel.querySelector('#map-source').addEventListener('change', () => {
      const selected = selectedCase();
      const outage = $('#lab-outage').value;
      const current = outage ? selected.source_outages[outage] : selected.base;
      drawWeightMap(selected.variable, outage, current.sources || []);
    });
  }
  const select = $('#map-source');
  const previous = select.value;
  select.innerHTML = sources.map(item => `<option value="${escapeHtml(item.source_id)}">${escapeHtml(names[item.source_id] || titleCase(item.source_id))}</option>`).join('');
  select.value = sources.some(item => item.source_id === previous) ? previous : sources[0]?.source_id || '';
  const source = select.value;
  const points = demo.cases.filter(item => item.variable === variable && item.selection.startsWith('latest')).map(item => {
    const receipt = excluded ? item.source_outages[excluded] : item.base;
    const weight = receipt?.sources?.find(row => row.source_id === source)?.weight;
    return { id: item.location_id, lat: item.base.target.latitude, lon: item.base.target.longitude, weight: Number.isFinite(weight) ? weight : null };
  }).filter(item => item.weight !== null);
  const x = lon => 64 + (lon - 68) / 28 * 760;
  const y = lat => 303 - (lat - 8) / 28 * 270;
  const grid = [10, 15, 20, 25, 30, 35].map(lat => `<line x1="64" y1="${y(lat)}" x2="824" y2="${y(lat)}" stroke="#e7eeee"/><text x="15" y="${y(lat) + 4}" fill="#8ca1a7" font-size="11">${lat}°N</text>`).join('') + [70, 75, 80, 85, 90, 95].map(lon => `<line x1="${x(lon)}" y1="33" x2="${x(lon)}" y2="303" stroke="#e7eeee"/><text x="${x(lon) - 14}" y="327" fill="#8ca1a7" font-size="11">${lon}°E</text>`).join('');
  const markers = points.map(item => {
    const cx = x(item.lon), cy = y(item.lat);
    const radius = 7 + 18 * Math.sqrt(item.weight);
    const opacity = .25 + .75 * item.weight;
    return `<circle cx="${cx}" cy="${cy}" r="${radius + 4}" fill="#0da69b" opacity=".10"/><circle cx="${cx}" cy="${cy}" r="${radius}" fill="#0da69b" opacity="${opacity}" stroke="#087f7a" stroke-width="1"/><text x="${cx + radius + 7}" y="${cy - 4}" fill="#274f5b" font-size="12" font-weight="700">${escapeHtml(titleCase(item.id))}</text><text x="${cx + radius + 7}" y="${cy + 12}" fill="#75929a" font-size="11">${pct(item.weight)}</text>`;
  }).join('');
  $('#weight-map').innerHTML = grid + markers;
  $('#map-range').textContent = points.length ? `${points.length} locations · ${pct(Math.min(...points.map(p => p.weight)))}–${pct(Math.max(...points.map(p => p.weight)))} mixture share` : 'No eligible site weights in this scenario.';
}

function renderEvidence() {
  if (!benchmark) return;
  const variable = $('#evidence-variable').value;
  const data = benchmark.variables[variable];
  if (!data) return;
  const methods = Object.entries(data.methods).filter(([, row]) => Number.isFinite(row.proper_score)).sort((a, b) => a[1].proper_score - b[1].proper_score);
  const worst = Math.max(...methods.map(([, row]) => row.proper_score));
  const readable = { xgboost_distributional_stack: 'XGBoost distributional stack', emos_adapted: 'Adapted EMOS', bma_fixed_components: 'Fixed-component BMA', calibrated_equal: 'Calibrated equal blend', inverse_skill: 'Inverse skill', static: 'Static blend', interpretable: 'Interpretable adaptive gate', spatial: 'Spatial attention challenger' };
  const champion = methods[0];
  $('#evidence-content').innerHTML = `<div class="evidence-grid"><div class="evidence-card"><div class="eyebrow ink">HELD-OUT CASES</div><strong>${data.split_counts.test}</strong><span>Per target after development selection</span></div><div class="evidence-card"><div class="eyebrow ink">BEST SCORE</div><strong>${fmt(champion[1].proper_score, 3)}</strong><span>${escapeHtml(readable[champion[0]] || champion[0])} · lower is better</span></div><div class="evidence-card"><div class="eyebrow ink">RESEARCH TRACKS</div><strong>${methods.length}</strong><span>Available methods on this target</span></div></div><div class="score-table"><div class="eyebrow ink">FAIR COMPARISON</div><h2>Proper score by method</h2>${methods.map(([name, row]) => `<div class="score-row"><span class="score-name">${escapeHtml(readable[name] || titleCase(name))}</span><span class="score-value">${fmt(row.proper_score, 3)}</span><div class="score-track"><span style="width:${Math.max(2, 100 * row.proper_score / worst)}%;background:${name === 'interpretable' ? '#0e9b92' : name === champion[0] ? '#377fc3' : '#afc6cc'}"></span></div></div>`).join('')}<p class="fineprint">${escapeHtml(variable === 'wind_uv' ? 'Joint wind energy score.' : 'Continuous ranked probability score (CRPS).')} The interpretable gate is a documented research track; it is not declared best when a stronger method wins.</p></div><div class="two-up below"><article class="card"><div class="eyebrow ink">ABLATION LOGIC</div><h2>What we remove and why</h2><p class="fineprint">The research release tests source subsets, context groups, routing forms and source outages. Any gain must survive blocked time, region and extreme-event verification on actual observations before operational claims.</p><div class="dark-tags" style="margin-top:16px"><span style="color:#2b7774;background:#e7f7f4">Sources</span><span style="color:#2b7774;background:#e7f7f4">Regime</span><span style="color:#2b7774;background:#e7f7f4">Recent skill</span><span style="color:#2b7774;background:#e7f7f4">Calibration</span></div></article><article class="card"><div class="eyebrow ink">LIMITATIONS</div><h2>What the score cannot say</h2>${benchmark.limitations.map(text => `<p class="fineprint" style="margin:8px 0">• ${escapeHtml(text)}</p>`).join('')}</article></div>`;
}

function renderAtlas() {
  if (!catalogue) return;
  const query = $('#model-search').value.trim().toLowerCase();
  const family = $('#model-family').value;
  const filtered = catalogue.models.filter(model => (family === 'all' || model.model_class === family) && `${model.name} ${model.id} ${model.provider || ''} ${model.purpose || ''}`.toLowerCase().includes(query));
  $('#model-count').textContent = `${filtered.length} / ${catalogue.models.length} profiles`;
  $('#model-grid').innerHTML = filtered.map(model => `<article class="model-card"><div class="model-top"><span class="model-family">${escapeHtml(model.model_class)}</span><span class="scope-badge synthetic">CANDIDATE</span></div><h3>${escapeHtml(model.name)}</h3><div class="model-id">${escapeHtml(model.id)} · ${escapeHtml(model.version || 'version unpinned')}</div><p>${escapeHtml(model.purpose || model.description || 'Research candidate.')}</p><div class="capabilities">${Object.entries(model.capabilities || {}).filter(([, value]) => value === true).map(([name]) => `<span>${escapeHtml(name.replace('_uv', ' vector'))}</span>`).join('') || '<span>Capability audit needed</span>'}</div><div class="model-meta"><b>Readiness:</b> ${escapeHtml(model.status || 'not verified')}<br><b>India skill:</b> ${model.india_skill_metrics ? 'See evidence' : 'Not established'}<br><b>Archive:</b> ${model.archive_verified ? 'Verified' : 'Not verified'}</div><details><summary>Access and evidence</summary><p>${escapeHtml(model.access || model.description || 'Access audit required.')}</p>${(model.evidence_urls || []).slice(0, 2).map(url => { try { const parsed = new URL(url); return parsed.protocol === 'https:' ? `<p><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(parsed.hostname)} ↗</a></p>` : ''; } catch { return ''; } }).join('')}</details></article>`).join('') || '<div class="empty">No matching profiles.</div>';
}

async function init() {
  const cities = ['Delhi', 'Mumbai', 'Bengaluru', 'Chennai', 'Kolkata', 'Hyderabad', 'Guwahati', 'Jaipur'];
  $('#public-city').innerHTML = cities.map(city => `<option>${city}</option>`).join('');
  $('#public-city').value = 'Mumbai';
  $('#public-city').addEventListener('change', loadPublic);
  $('#public-variable').addEventListener('change', renderPublic);
  $('#lab-variable').addEventListener('change', syncLabLocations);
  $('#lab-location').addEventListener('change', syncLabCases);
  $('#lab-case').addEventListener('change', syncLabOutages);
  $('#lab-outage').addEventListener('change', renderLab);
  $('#evidence-variable').addEventListener('change', renderEvidence);
  $('#model-search').addEventListener('input', renderAtlas);
  $('#model-family').addEventListener('change', renderAtlas);
  $$('[data-go]').forEach(button => button.addEventListener('click', () => { location.hash = button.dataset.go; }));
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));
  try {
    [demo, benchmark, catalogue] = await Promise.all([getJson('/data/demo.json'), getJson('/data/benchmark.json'), getJson('/data/catalogue.json')]);
    if (demo.data_kind !== 'synthetic' || benchmark.data_kind !== 'synthetic' || demo.model_digest !== benchmark.source_model_digest) throw new Error('Research export provenance mismatch');
    syncLabLocations();
  } catch (error) {
    $('#lab-message').className = 'error';
    $('#lab-message').textContent = `Research export unavailable: ${error.message}`;
    $('#evidence-content').textContent = 'Research benchmark unavailable.';
    $('#model-grid').textContent = 'Model atlas unavailable.';
  }
  navigate(location.hash.slice(1) || 'overview');
}
init();
