import {readJSON} from './weather.js';
export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fmt=v=>Number.isFinite(v)?v.toLocaleString('en-IN',{maximumFractionDigits:1}):'Unavailable';
export const ist=t=>t?new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',dateStyle:'medium',timeStyle:'short'}).format(new Date(t))+' IST':'Unavailable';
export const read=url=>fetch(url,{signal:AbortSignal.timeout(60000)}).then(readJSON);
export const table=(head,rows)=>`<div class="table-wrap"><table><thead><tr>${head.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
export function indiaSampleGrid(geo){
  const insideRing=(ring,lon,lat)=>{let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if((a[1]>lat)!==(b[1]>lat)&&lon<(b[0]-a[0])*(lat-a[1])/(b[1]-a[1])+a[0])inside=!inside;}return inside;};
  const inside=(lon,lat)=>geo.features.some(f=>{const polygons=f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.coordinates;return polygons.some(poly=>insideRing(poly[0],lon,lat)&&!poly.slice(1).some(hole=>insideRing(hole,lon,lat)));});
  const points=[];
  for(let lat=8;lat<=36;lat+=3)for(let lon=69;lon<=97;lon+=3)if(inside(lon,lat))points.push({latitude:lat,longitude:lon});
  for(const [latitude,longitude] of [[11.67,92.75],[10.57,72.64]])if(inside(longitude,latitude))points.push({latitude,longitude});
  return points.map(({latitude,longitude})=>({label:`${latitude.toFixed(2)}°N, ${longitude.toFixed(2)}°E`,latitude,longitude,parameter:new URLSearchParams({latitude,longitude}).toString()}));
}
export async function samplesFor(selection,action,onProgress=()=>{},geo){
  const requests=[selection,...(geo?indiaSampleGrid(geo):[]).filter(p=>!Number.isFinite(selection.latitude)||!Number.isFinite(selection.longitude)||Math.hypot(p.latitude-selection.latitude,p.longitude-selection.longitude)>.25)];
  const results=new Array(requests.length);let next=0,done=0;
  const worker=async()=>{while(next<requests.length){const i=next++,s=requests[i];try{results[i]={...s,data:await read(`/api/intelligence?action=${action}&${s.parameter}`)};}catch(e){results[i]={...s,error:e.message};}onProgress(++done,requests.length);}};
  await Promise.all([worker(),worker(),worker()]);return results;
}
export function mapSVG(geo,points,{color='#167f8b',shape='circle',zoom=1,center,idPrefix='forecast'}={}){
  const project=([lon,lat])=>[(lon-67)*15+25,(38-lat)*16+20];
  const rings=geo.features.flatMap(f=>f.geometry.type==='Polygon'?f.geometry.coordinates:f.geometry.coordinates.flat());
  const path=rings.map(r=>r.map((p,i)=>`${i?'L':'M'}${project(p).join(',')}`).join(' ')+'Z').join(' ');
  const focus=points.find(p=>p.selected),[cx,cy]=center|| (focus?project([focus.longitude,focus.latitude]):[260,285]);
  const w=520/zoom,h=570/zoom,x=zoom===1?0:Math.max(0,Math.min(520-w,cx-w/2)),y=zoom===1?0:Math.max(0,Math.min(570-h,cy-h/2));
  const markers=points.map((p,i)=>{const [px,py]=project([p.longitude,p.latitude]),r=p.selected?9:6,fill=p.color||color,showLabel=p.selected||zoom>=2.2,tx=px+13;
    return `<g tabindex="0" role="button" data-point="${i}" aria-label="${esc(p.label)}: ${esc(p.value)}"><title>${esc(p.label)}: ${esc(p.value)}</title><circle class="sample-halo" cx="${px}" cy="${py}" r="20" fill="url(#${idPrefix}-sample-${i})"/>${shape==='diamond'?`<path d="M${px},${py-r}l${r},${r}l-${r},${r}l-${r},-${r}Z"`:shape==='triangle'?`<path d="M${px},${py-r}l${r},${r*2}h-${r*2}Z"`:`<circle cx="${px}" cy="${py}" r="${r}"`} fill="${fill}" stroke="${p.selected?'#102f3d':'white'}" stroke-width="${p.selected?2.6:1.3}"/>${showLabel?`<text x="${tx}" y="${py-3}" class="map-city-label">${esc(p.label)}</text><text x="${tx}" y="${py+11}" class="map-number-label">${esc(p.value)}</text>`:''}</g>`;}).join('');
  return `<div class="map-zoom"><button data-zoom="in" aria-label="Zoom in">Zoom in +</button><button data-zoom="out" aria-label="Zoom out">Zoom out −</button><button data-zoom="reset">Full map</button><span>Drag to pan · scroll to zoom</span></div><svg class="readable-map" viewBox="${x} ${y} ${w} ${h}" role="group" aria-label="Retrieved forecast samples across India. Select a marker to inspect; grey land is unsampled."><defs>${points.map((p,i)=>`<radialGradient id="${idPrefix}-sample-${i}"><stop offset="0" stop-color="${p.color||color}" stop-opacity=".52"/><stop offset=".55" stop-color="${p.color||color}" stop-opacity=".18"/><stop offset="1" stop-color="${p.color||color}" stop-opacity="0"/></radialGradient>`).join('')}</defs><path d="${path}" fill="#e0e8e4" stroke="#94a8a0" stroke-width=".8" fill-rule="evenodd"/>${markers}</svg><p class="fineprint">Soft circles mark retrieved coordinates. There are no estimates between points or verified skill scores. Grey land has no data. Boundary: <a href="https://github.com/datameet/maps/tree/master/Country">DataMeet</a>, CC BY 4.0; illustrative.</p>`;
}
export function bindMap(host,state,draw){
  host.querySelectorAll('[data-point]').forEach(el=>{const select=()=>{state.selected=Number(el.dataset.point);state.center=null;draw();};el.addEventListener('click',select);el.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select();}});});
  host.querySelectorAll('[data-zoom]').forEach(el=>el.addEventListener('click',()=>{state.zoom=el.dataset.zoom==='reset'?1:Math.max(1,Math.min(8,state.zoom*(el.dataset.zoom==='in'?1.6:1/1.6)));if(el.dataset.zoom==='reset')state.center=null;draw();}));
  const svg=host.querySelector('.readable-map');if(!svg)return;
  svg.addEventListener('wheel',e=>{e.preventDefault();const box=svg.viewBox.baseVal,rect=svg.getBoundingClientRect(),fx=(e.clientX-rect.left)/rect.width,fy=(e.clientY-rect.top)/rect.height;state.center=[box.x+box.width*fx,box.y+box.height*fy];state.zoom=Math.max(1,Math.min(8,state.zoom*(e.deltaY<0?1.25:.8)));draw();},{passive:false});
  let drag=null;svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;const b=svg.viewBox.baseVal;drag={x:e.clientX,y:e.clientY,bx:b.x,by:b.y,w:b.width,h:b.height};svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag||state.zoom===1)return;const rect=svg.getBoundingClientRect(),x=Math.max(0,Math.min(520-drag.w,drag.bx-(e.clientX-drag.x)*drag.w/rect.width)),y=Math.max(0,Math.min(570-drag.h,drag.by-(e.clientY-drag.y)*drag.h/rect.height));svg.setAttribute('viewBox',`${x} ${y} ${drag.w} ${drag.h}`);state.center=[x+drag.w/2,y+drag.h/2];});
  svg.addEventListener('pointerup',()=>{drag=null;});svg.addEventListener('pointercancel',()=>{drag=null;});
}
