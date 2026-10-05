import {readJSON} from './weather.js';
const $=id=>document.getElementById(id);
let messages=[],busy=false;
function bubble(role,text) {
  const element=document.createElement('div');element.className=`chat-message ${role}`;
  const label=document.createElement('strong');label.textContent=role==='user'?'You':'Anvaya';
  const body=document.createElement('p');body.textContent=text;
  element.append(label,body);$('chat-log').append(element);element.scrollIntoView({block:'nearest'});return element;
}
export function openChat(prompt) {
  $('chat-panel').hidden=false;$('chat-launcher').setAttribute('aria-expanded','true');
  if(prompt)$('chat-input').value=prompt;$('chat-input').focus();
}
async function send(event) {
  event.preventDefault();const text=$('chat-input').value.trim();if(!text||busy)return;
  busy=true;$('chat-send').disabled=true;$('chat-input').value='';
  bubble('user',text);messages.push({role:'user',content:text});
  const pending=bubble('assistant','Checking the evidence…');pending.classList.add('pending');
  $('chat-state').textContent='Looking up sources…';
  try {
    const recent=[];let size=0;for(const message of messages.slice(-11).reverse()){if(size+message.content.length>17000)break;recent.unshift(message);size+=message.content.length;}
    const data=await readJSON(await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:recent,context:window.anvayaContext||{view:location.hash}}),signal:AbortSignal.timeout(120000)}));
    pending.querySelector('p').textContent=data.answer;pending.classList.remove('pending');messages.push({role:'assistant',content:data.answer});
    if(data.steps?.length){const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=`Evidence trail · ${data.steps.length} lookups`;details.append(summary);for(const step of data.steps){const p=document.createElement('p');p.textContent=`${step.status==='complete'?'✓':'!'} ${step.summary}`;details.append(p);}pending.append(details);}
    if(data.sources?.length){const links=document.createElement('div');links.className='chat-sources';for(const source of data.sources.slice(0,6)){if(!/^https:\/\//.test(source.url))continue;const a=document.createElement('a');a.href=source.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=source.label;links.append(a);}pending.append(links);}
    $('chat-state').textContent='Groq connected · source-backed answers';
  }catch(error){pending.querySelector('p').textContent=error.name==='TimeoutError'?'The assistant timed out. Try a shorter question.':error.message;pending.classList.remove('pending');pending.classList.add('chat-error');messages.pop();$('chat-state').textContent='Could not complete this request';}
  finally{busy=false;$('chat-send').disabled=false;pending.scrollIntoView({block:'nearest'});$('chat-input').focus();}
}
export function setupChat() {
  $('chat-launcher').addEventListener('click',()=>{if($('chat-panel').hidden)openChat();else{$('chat-panel').hidden=true;$('chat-launcher').setAttribute('aria-expanded','false');}});
  const close=()=>{$('chat-panel').hidden=true;$('chat-launcher').setAttribute('aria-expanded','false');$('chat-launcher').focus();};
  $('chat-close').addEventListener('click',close);
  $('chat-panel').addEventListener('keydown',event=>{if(event.key==='Escape')close();});
  $('chat-form').addEventListener('submit',send);
  $('chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();$('chat-form').requestSubmit();}});
  document.querySelectorAll('[data-chat-prompt]').forEach(button=>button.addEventListener('click',()=>openChat(button.dataset.chatPrompt)));
  $('chat-clear').addEventListener('click',()=>{if(busy)return;messages=[];$('chat-log').replaceChildren();bubble('assistant','Start with a place, a forecast question, or a model you want to understand.');});
  fetch('/api/chat').then(readJSON).then(data=>{$('chat-state').textContent=data.configured?'Groq connected · ready to look up weather':'Assistant temporarily unavailable';$('chat-setup').hidden=data.configured;$('chat-send').disabled=!data.configured;$('chat-input').disabled=!data.configured;}).catch(()=>{$('chat-state').textContent='Assistant service unavailable';$('chat-send').disabled=true;$('chat-input').disabled=true;});
}
