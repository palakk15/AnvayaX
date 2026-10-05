import {readJSON} from './weather.js';
export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fmt=v=>Number.isFinite(v)?v.toLocaleString('en-IN',{maximumFractionDigits:1}):'Unavailable';
export const ist=t=>t?new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',dateStyle:'medium',timeStyle:'short'}).format(new Date(t))+' IST':'Unavailable';
export const read=url=>fetch(url,{signal:AbortSignal.timeout(60000)}).then(readJSON);
export const table=(head,rows)=>`<div class="table-wrap"><table><thead><tr>${head.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
export async function samplesFor(selection,action,onProgress=()=>{}){
  const requests=[selection,...['Delhi','Bengaluru','Chennai','Guwahati','Kolkata','Jaipur','Hyderabad','Mumbai'].filter(c=>selection.label.split(',')[0].toLowerCase()!==c.toLowerCase()).map(label=>({label,parameter:`city=${label}`}))];
  const results=new Array(requests.length);let next=0,done=0;
  const worker=async()=>{while(next<requests.length){const i=next++,s=requests[i];try{results[i]={...s,data:await read(`/api/intelligence?action=${action}&${s.parameter}`)};}catch(e){results[i]={...s,error:e.message};}onProgress(++done,requests.length);}};
  await Promise.all([worker(),worker()]);return results;
}
export function mapSVG(geo,points,{color='#167f8b',shape='circle',zoom=1}={}){
  const project=([lon,lat])=>[(lon-67)*15+25,(38-lat)*16+20];
  const rings=geo.features.flatMap(f=>f.geometry.type==='Polygon'?f.geometry.coordinates:f.geometry.coordinates.flat());
  const path=rings.map(r=>r.map((p,i)=>`${i?'L':'M'}${project(p).join(',')}`).join(' ')+'Z').join(' ');
  const focus=points.find(p=>p.selected),[cx,cy]=focus?project([focus.longitude,focus.latitude]):[260,285];
  const w=520/zoom,h=570/zoom,x=zoom===1?0:Math.max(0,Math.min(520-w,cx-w/2)),y=zoom===1?0:Math.max(0,Math.min(570-h,cy-h/2));
  return `<div class="map-zoom"><button data-zoom="in" aria-label="Zoom in on selected city">Zoom in +</button><button data-zoom="out" aria-label="Zoom out">Zoom out −</button><button data-zoom="reset">Full map</button></div><svg class="readable-map" viewBox="${x} ${y} ${w} ${h}" role="group" aria-label="Sampled city estimates; select a marker to inspect. Grey land has no sampled data."><path d="${path}" fill="#e0e8e4" stroke="#94a8a0" stroke-width=".8" fill-rule="evenodd"/>${points.map((p,i)=>{const [px,py]=project([p.longitude,p.latitude]),r=12,fill=p.color||color,left=p.longitude<79&&p.latitude<28,tx=px+(left?-18:18),anchor=left?'end':'start';return `<g tabindex="0" role="button" data-point="${i}" aria-label="${esc(p.label)}: ${esc(p.value)}"><title>${esc(p.label)}: ${esc(p.value)}</title><circle cx="${px}" cy="${py}" r="${p.selected?23:18}" fill="${fill}" opacity=".18"/>${shape==='diamond'?`<path d="M${px},${py-r}l${r},${r}l-${r},${r}l-${r},-${r}Z"`:shape==='triangle'?`<path d="M${px},${py-r}l${r},${r*2}h-${r*2}Z"`:`<circle cx="${px}" cy="${py}" r="${r}"`} fill="${fill}" stroke="${p.selected?'#102f3d':'white'}" stroke-width="2"/><text x="${tx}" y="${py-3}" text-anchor="${anchor}" class="map-city-label">${esc(p.label.split(',')[0])}</text><text x="${tx}" y="${py+13}" text-anchor="${anchor}" class="map-number-label">${esc(p.value)}</text></g>`;}).join('')}</svg><p class="fineprint">City samples only; grey land has no estimate. No interpolation across India. Boundary: <a href="https://github.com/datameet/maps/tree/master/Country">DataMeet</a>, CC BY 4.0; illustrative.</p>`;
}
export function bindMap(host,state,draw){
  host.querySelectorAll('[data-point]').forEach(el=>{const select=()=>{state.selected=Number(el.dataset.point);draw();};el.addEventListener('click',select);el.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select();}});});
  host.querySelectorAll('[data-zoom]').forEach(el=>el.addEventListener('click',()=>{state.zoom=el.dataset.zoom==='reset'?1:Math.max(1,Math.min(3,state.zoom+(el.dataset.zoom==='in'?.5:-.5)));draw();}));
}
