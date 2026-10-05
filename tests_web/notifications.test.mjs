import test from 'node:test';
import assert from 'node:assert/strict';
import {highImpactSignals} from '../site/notifications.js';

const event=(above,below,threshold=10)=>({threshold,members:[...Array(above)].map(()=>({value:threshold+1})).concat([...Array(below)].map(()=>({value:threshold-1})))});

test('high-impact notices require a majority and enough complete members',()=>{
  const signals=highImpactSignals({events:[event(6,4),event(4,6),event(5,4)]});
  assert.deepEqual(signals.map(s=>s.label),['Heavy rainfall']);
  assert.equal(signals[0].count,6);
  assert.equal(signals[0].total,10);
});

test('missing event data cannot create an emergency signal',()=>{
  assert.deepEqual(highImpactSignals({events:[{threshold:10,members:[]},{threshold:null,members:Array(20).fill({value:50})}]}),[]);
});
