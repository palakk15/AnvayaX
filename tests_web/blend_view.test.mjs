import test from 'node:test';
import assert from 'node:assert/strict';
import {selectedForecast,disagreementSummary,forecastArtifact} from '../site/blend_view.js';
import {GET} from '../api/public-forecast.mjs';
const series=Array.from({length:30},(_,i)=>({time:new Date(Date.UTC(2026,8,30,i)).toISOString(),temperature:{value:20+i,weights:{gfs_global:.5,icon_global:.5},predictions:{gfs_global:19+i,icon_global:21+i}},rainfall:{value:i,weights:{},predictions:{}},wind_uv:{value:[3,4],speed:5,direction:90,weights:{},predictions:{}}}));
test('chosen hour drives point values and the matching rolling precipitation window',()=>{
  const a=selectedForecast(series,0),b=selectedForecast(series,6);
  assert.equal(a.row.temperature.value,20);assert.equal(b.row.temperature.value,26);
  assert.equal(a.rain24,276);assert.equal(b.rain24,420);
  assert.equal(selectedForecast(series,7).rain24,null);
  assert.equal(selectedForecast(series,31),null);
});
test('plain language disagreement uses same-hour values and handles missing competitors',()=>{
  const d=disagreementSummary(series[6],'temperature');
  assert.equal(d.low.value,25);assert.equal(d.high.value,27);assert.equal(d.range,2);
  assert.match(d.explanation,/25/);assert.match(d.explanation,/27/);
  assert.equal(disagreementSummary({...series[0],temperature:{predictions:{gfs_global:20}}},'temperature').range,null);
});
test('forecast artifact includes the full timeline and marks the selected timestamp',()=>{
  const html=forecastArtifact({series,location:{name:'Delhi'}},6,'temperature');
  assert.equal((html.match(/data-inspect-hour=/g)||[]).length,30);
  assert.match(html,/data-inspect-hour="6" aria-pressed="true"/);
  assert.match(html,/Download forecast CSV/);
});
test('forecast requests require an explicit location instead of defaulting to Mumbai',async()=>{
  const response=await GET(new Request('http://localhost/api/public-forecast'));
  assert.equal(response.status,400);assert.match((await response.json()).error,/Choose a city/);
});
