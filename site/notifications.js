import {eventStats} from './risk.js';
import {esc,ist} from './map_shared.js';

const labels=['Heavy rainfall','High temperature','High wind'];
const minimumMembers=10;
const minimumShare=.5;
const alerted=new Set();

export function highImpactSignals(data){
  if(!Array.isArray(data?.events))return [];
  return data.events.flatMap((event,index)=>{
    if(!event?.members||!Number.isFinite(event.threshold))return [];
    const stats=eventStats(event);
    return stats.n>=minimumMembers&&stats.probability>=minimumShare
      ?[{index,label:labels[index]||'High-impact weather',count:stats.count,total:stats.n,threshold:event.threshold}]
      :[];
  });
}

function browserAvailable(){return typeof window!=='undefined'&&window.isSecureContext&&'Notification' in window;}
function notify(data,signals){
  if(!browserAvailable()||Notification.permission!=='granted')return;
  const location=data.location?.name||data.location?.label||'Selected location';
  for(const signal of signals){
    const id=[data.location?.latitude,data.location?.longitude,data.window_start,data.window_end,signal.index].join(':');
    if(alerted.has(id))continue;
    try{
      const note=new Notification(`Forecast signal near ${location}`,{
        body:`${signal.label}: ${signal.count} of ${signal.total} GEFS members exceed the screening cutoff. Check official IMD or NDMA alerts.`,
        tag:`anvayax-${id}`,icon:'/favicon.svg'
      });
      note.onclick=()=>{window.focus();window.location.hash='extreme';note.close();};
      alerted.add(id);
    }catch{
      // Some mobile browsers grant permission but do not support this API.
      return;
    }
  }
}

export function renderForecastNotifications(host,data){
  const signals=highImpactSignals(data),available=browserAvailable();
  const permission=available?Notification.permission:'unavailable',enabled=permission==='granted';
  host.innerHTML=`<section class="forecast-notice ${signals.length?'is-active':''}" aria-live="polite"><div><span class="eyebrow">OPT-IN FORECAST NOTICES</span><h2>${signals.length?'High-impact forecast signal':'Watch this forecast window'}</h2><p>${signals.length?`${esc(signals.map(s=>`${s.label}: ${s.count} of ${s.total} complete members`).join(' · '))}. Window: ${esc(ist(data.window_start))} to ${esc(ist(data.window_end))}.`:'No severe screening cutoff has a majority of complete GEFS members in this window. This is not an all-clear.'}</p><p>Experimental model-member signal, not an official emergency alert or a calibrated event probability. Check <a href="https://mausam.imd.gov.in/" target="_blank" rel="noopener noreferrer">IMD warnings</a> and <a href="https://sachet.ndma.gov.in/" target="_blank" rel="noopener noreferrer">NDMA Sachet</a> before acting.</p></div><div class="notice-actions"><button type="button" class="notice-enable" ${!available||enabled||permission==='denied'?'disabled':''}>${enabled?'Browser notices enabled':permission==='denied'?'Browser notices blocked':'Enable browser notices'}</button><small role="status">${available?'Notices appear only while this page is open and a new strong signal is retrieved.':'This browser cannot show page-based notices; the signal above remains visible.'}</small></div></section>`;
  host.querySelector('.notice-enable').onclick=async()=>{
    if(!browserAvailable())return;
    const permission=await Notification.requestPermission();
    if(permission==='granted')notify(data,signals);
    renderForecastNotifications(host,data);
  };
  if(enabled)notify(data,signals);
}
