import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { additiveForward, spatialForward, scenarioRows, mixtureFromFixedSources } from '../site/gate.js';

const gate = JSON.parse(await readFile(new URL('../site/data/gate.json', import.meta.url), 'utf8'));
const demo = JSON.parse(await readFile(new URL('../site/data/demo.json', import.meta.url), 'utf8'));
const catalogue = JSON.parse(await readFile(new URL('../site/data/catalogue.json', import.meta.url), 'utf8'));

test('browser additive gate reproduces every exported research receipt', () => {
  for (const item of demo.cases) {
    const model = gate.variables[item.variable];
    const mask = model.sources.map(source => item.base.sources.some(row => row.source_id === source));
    const prediction = additiveForward(model, item.patch_rows[0], mask);
    for (let index = 0; index < model.sources.length; index++) {
      const expected = item.base.sources.find(row => row.source_id === model.sources[index]);
      if (expected) {
        assert.ok(Math.abs(prediction.weights[index] - expected.weight) < 1e-8, `${item.location_id} ${item.variable} ${model.sources[index]}`);
        assert.ok(Math.abs(prediction.logits[index] - expected.logit) < 1e-8);
      } else assert.equal(prediction.weights[index], 0);
    }
  }
});

test('browser spatial transformer reproduces the saved PyTorch output', () => {
  for (const item of demo.cases) {
    const model = gate.variables[item.variable];
    const mask = model.sources.map(source => item.base.sources.some(row => row.source_id === source));
    const prediction = spatialForward(model, item.patch_rows, mask);
    for (let index = 0; index < model.sources.length; index++) {
      assert.ok(Math.abs(prediction.weights[index] - item.spatial_reference.weights[index]) < 2e-4,
        `${item.location_id} ${item.variable} ${model.sources[index]}: ${prediction.weights[index]} vs ${item.spatial_reference.weights[index]}`);
    }
  }
});

test('editable context preserves base receipt and responds to source removal', () => {
  const item = demo.cases.find(row => row.variable === 'rainfall' && row.location_id === 'mumbai' && row.selection.startsWith('latest'));
  const model = gate.variables.rainfall;
  const raw = item.patch_rows[0];
  const values = {
    date: item.base.target.valid_start.slice(0, 10), lead: raw['lead.hours'],
    coast: raw['geography.coast'], elevation: raw['geography.elevation'],
    sources: Object.fromEntries(model.sources.map(source => [source, {
      signal: raw[`regime.source_${source}`], skill: raw[`recent_skill.${source}`],
      age: raw[`age.${source}`], spread: raw[`disagreement.spread_${source}`],
      available: Boolean(raw[`availability.${source}`]),
    }])),
  };
  const patch = scenarioRows(item, values, model.sources);
  const base = additiveForward(model, patch[0], model.sources.map(source => values.sources[source].available));
  assert.ok(Math.abs(base.weights[0] - item.base.sources[0].weight) < 1e-8);
  const blend = mixtureFromFixedSources(item, model.sources, base.weights);
  assert.ok(Math.abs(blend.mean - item.base.summary.mean) < 1e-8);
  assert.ok(Math.abs(blend.events[0].probability - item.base.probabilities[0].probability) < 1e-8);
  values.sources[model.sources[0]].available = false;
  const removed = additiveForward(model, scenarioRows(item, values, model.sources)[0], model.sources.map(source => values.sources[source].available));
  assert.equal(removed.weights[0], 0);
  assert.ok(Math.abs(removed.weights.reduce((a, b) => a + b, 0) - 1) < 1e-12);
});

test('every saved case resets to its exported additive routing decision', () => {
  for (const item of demo.cases) {
    const model = gate.variables[item.variable];
    const raw = item.patch_rows[0];
    const values = {
      date: item.base.target.valid_start.slice(0, 10), lead: raw['lead.hours'],
      coast: raw['geography.coast'], elevation: raw['geography.elevation'],
      sources: Object.fromEntries(model.sources.map(source => [source, {
        signal: raw[`regime.source_${source}`], skill: raw[`recent_skill.${source}`],
        age: raw[`age.${source}`], spread: raw[`disagreement.spread_${source}`],
        available: Boolean(raw[`availability.${source}`]),
      }])),
    };
    const mask = model.sources.map(source => values.sources[source].available);
    const prediction = additiveForward(model, scenarioRows(item, values, model.sources)[0], mask);
    model.sources.forEach((source, index) => {
      const expected = item.base.sources.find(row => row.source_id === source)?.weight || 0;
      assert.ok(Math.abs(prediction.weights[index] - expected) < 1e-8,
        `${item.location_id} ${item.variable} ${source}`);
    });
  }
});

test('public site excludes presentation guide and every model explains four contexts', async () => {
  await assert.rejects(stat(new URL('../site/ppt.md', import.meta.url)), { code: 'ENOENT' });
  const html = await readFile(new URL('../site/index.html', import.meta.url), 'utf8');
  assert.doesNotMatch(html, /ppt\.md|presentation guide|No real-world skill claim/i);
  assert.equal(catalogue.models.length, 35);
  for (const model of catalogue.models) {
    for (const axis of ['region', 'season', 'lead', 'weather', 'analysis']) {
      assert.ok(model.use_context?.[axis]?.length > 35, `${model.id} ${axis}`);
    }
  }
});
