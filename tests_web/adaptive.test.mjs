import test from 'node:test';
import assert from 'node:assert/strict';
import {adaptiveBlend} from '../server/intelligence.mjs';
import {answerChat} from '../server/assistant.mjs';

test('adaptive allocation responds to forecasts and softens with horizon while preserving diversity',()=>{
  const early=adaptiveBlend({a:20,b:21,c:29},'temperature',1);
  const late=adaptiveBlend({a:20,b:21,c:29},'temperature',96);
  assert.ok(early.weights.b>early.weights.c);
  assert.ok(late.weights.c>early.weights.c);
  assert.ok(Object.values(early.weights).every(w=>w>=1/6));
  assert.ok(Math.abs(Object.values(early.weights).reduce((a,b)=>a+b)-1)<1e-12);
  assert.equal(early.used_historical_skill,false);
  const changed=adaptiveBlend({a:29,b:21,c:20},'temperature',1);
  assert.ok(Math.abs(changed.weights.a-early.weights.c)<1e-12);
});
test('agreement router handles empty sources, identical forecasts and opposing wind vectors',()=>{
  assert.equal(adaptiveBlend({},'temperature',1,['a','b']).value,null);
  assert.deepEqual(adaptiveBlend({a:20,b:20,c:null},'temperature',1).weights,{a:.5,b:.5,c:0});
  const wind=adaptiveBlend({a:[0,10],b:[0,-10]},'wind_uv',1);
  assert.equal(wind.speed,0);assert.equal(wind.direction,null);
});
test('chat validates a session key before contacting the provider',async()=>{
  await assert.rejects(answerChat({messages:[{role:'user',content:'Hello'}],apiKey:'invalid'}),/valid Groq API key/);
  const result=await answerChat({messages:[{role:'user',content:'Hello'}],apiKey:'gsk_'+'a'.repeat(30)},{complete:async()=>({content:'Connected'})});
  assert.equal(result.answer,'Connected');
});
