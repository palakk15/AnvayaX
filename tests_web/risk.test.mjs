import test from 'node:test';
import assert from 'node:assert/strict';
import {eventStats,probabilityLabel} from '../site/risk.js';

test('zero exceedances remain zero and report the forecast magnitude',()=>{
  const event={threshold:64.5,members:Array.from({length:31},(_,i)=>({value:i/3}))};
  const result=eventStats(event);
  assert.equal(result.probability,0);assert.equal(result.max,10);
  assert.equal(result.median,5);assert.equal(result.count,0);
  assert.equal(probabilityLabel(result),'No members above threshold');
  assert.equal(eventStats(event,5).count,15);
});
test('missing ensemble coverage is unavailable rather than a false zero',()=>{
  const result=eventStats({threshold:40,members:[{value:30},{value:null},{value:NaN}]});
  assert.equal(result.n,1);assert.equal(result.probability,null);
  assert.equal(probabilityLabel(result),'Insufficient member coverage');
  assert.equal(eventStats({threshold:40,members:[]}).median,null);
});
test('strict thresholds and below-freezing temperatures retain numerical meaning',()=>{
  const event={threshold:-2,members:Array.from({length:10},(_,i)=>({value:i-5}))};
  const result=eventStats(event);
  assert.equal(result.min,-5);assert.equal(result.max,4);
  assert.equal(result.count,6);assert.equal(result.probability,.6);
  assert.equal(eventStats(event,4).probability,0);
  assert.equal(eventStats(event,-6).probability,1);
});
