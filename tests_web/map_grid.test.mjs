import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {indiaSampleGrid,mapSVG,kernelDensity} from '../site/map_shared.js';

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

test('density graph includes only returned finite samples and retains explicit coverage',()=>{
  assert.equal(kernelDensity([2,3]),null);
  const density=kernelDensity([.2,.3,.4,Number.NaN],{domain:[0,1]});
  assert.equal(density.n,3);
  assert.deepEqual(density.observations,[.2,.3,.4]);
  assert.equal(density.lo,0);
  assert.equal(density.hi,1);
  assert.ok(density.points.every(point=>Number.isFinite(point.density)));
});
