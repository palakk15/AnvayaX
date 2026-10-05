import { answerChat, groqKey } from '../server/assistant.mjs';
import { errorResponse, ServiceError } from '../server/http.mjs';
const usage = new Map();
export async function GET() { return Response.json({configured:Boolean(groqKey()),model:process.env.GROQ_MODEL||'openai/gpt-oss-120b',capabilities:['Place and PIN search','Selected live forecast explanation','Live model comparison','35-model atlas explanations']},{headers:{'Cache-Control':'no-store'}}); }
export async function POST(request) {
  try {
    const origin=request.headers.get('origin');
    if(origin && new URL(origin).host!==new URL(request.url).host) throw new ServiceError('Use the assistant from this website.',403);
    if(!request.headers.get('content-type')?.includes('application/json'))throw new ServiceError('Send a JSON message.',415);
    const id=request.headers.get('x-forwarded-for')?.split(',')[0] || 'local';
    const now=Date.now(), entry=usage.get(id);
    if(entry && entry.until>now && entry.count>=12)throw new ServiceError('Please wait a minute before sending more messages.',429);
    if(!entry||entry.until<=now){if(usage.size>=1000)usage.delete(usage.keys().next().value);usage.set(id,{count:1,until:now+60000});}else entry.count++;
    if(Number(request.headers.get('content-length'))>25000)throw new ServiceError('Message too long.',413);
    const reader=request.body?.getReader(); if(!reader)throw new ServiceError('Enter a message.',400);
    const chunks=[];let size=0;
    while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>25000){await reader.cancel();throw new ServiceError('Message too long.',413);}chunks.push(value);}
    let input;try{input=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new ServiceError('The message could not be read.',400);}
    return Response.json(await answerChat(input),{headers:{'Cache-Control':'no-store'}});
  } catch(error) { return errorResponse(error); }
}
