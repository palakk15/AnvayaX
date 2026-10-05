import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {indiaSampleGrid,mapSVG} from '../site/map_shared.js';

test('India map samples many distinct real request coordinates without a continuous skill layer',()=>{
  const geo=JSON.parse(readFileSync(new URL('../site/data/india.geojson',import.meta.url),'utf8'));
  const samples=indiaSampleGrid(geo);
  assert.ok(samples.length>=30);
  assert.equal(new Set(samples.map(s=>s.parameter)).size,samples.length);
  assert.ok(samples.some(s=>s.longitude>90));
  assert.ok(samples.some(s=>s.longitude<75));
  const html=mapSVG(geo,[{...samples[0],selected:true,value:'20%',color:'#147eb1'}],{zoom:2});
  assert.match(html,/scroll to zoom/);
  assert.match(html,/no estimates between points or verified skill scores/);
});
