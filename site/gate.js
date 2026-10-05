/** Browser inference for the exported synthetic research gates. No observation labels are used here. */
const clip = (value, low, high) => Math.min(high, Math.max(low, value));

export function featureVector(features, raw) {
  const vector = [1 - features.center[0]];
  for (let i = 0; i < features.names.length; i++) {
    const value = Number(raw[features.names[i]]);
    if (!Number.isFinite(value)) throw new Error(`Missing scenario feature: ${features.names[i]}`);
    const z = clip((value - features.mean[i]) / features.scale[i], -5, 5);
    const values = [z, Math.max(z + 1, 0), Math.max(z, 0), Math.max(z - 1, 0)];
    for (let j = 0; j < 4; j++) vector.push(values[j] - features.center[1 + i * 4 + j]);
  }
  return vector;
}

export function maskedSoftmax(logits, mask) {
  if (!mask.some(Boolean)) throw new Error('Select at least one eligible source');
  const maximum = Math.max(...logits.filter((_, index) => mask[index]));
  const exponentials = logits.map((value, index) => mask[index] ? Math.exp(value - maximum) : 0);
  const total = exponentials.reduce((a, b) => a + b, 0);
  return exponentials.map(value => value / total);
}

export function additiveForward(specification, raw, mask) {
  const model = specification.additive;
  const vector = featureVector(model.features, raw);
  const logits = specification.sources.map((_, source) =>
    vector.reduce((sum, value, feature) => sum + value * model.coefficients[feature][source], 0));
  const terms = specification.sources.map((_, source) => {
    const result = { intercept: vector[0] * model.coefficients[0][source] };
    model.features.names.forEach((name, index) => {
      let contribution = 0;
      for (let basis = 0; basis < 4; basis++) {
        const position = 1 + index * 4 + basis;
        contribution += vector[position] * model.coefficients[position][source];
      }
      result[name] = contribution;
    });
    return result;
  });
  return { logits, weights: maskedSoftmax(logits, mask), terms, vector };
}

function linear(input, weight, bias) {
  return weight.map((row, i) => row.reduce((sum, value, j) => sum + value * input[j], bias[i]));
}
function softmax(values) {
  const largest = Math.max(...values);
  const exps = values.map(value => Math.exp(value - largest));
  const total = exps.reduce((a, b) => a + b, 0);
  return exps.map(value => value / total);
}
function layerNorm(input, weight, bias) {
  const mean = input.reduce((a, b) => a + b, 0) / input.length;
  const variance = input.reduce((a, b) => a + (b - mean) ** 2, 0) / input.length;
  const divisor = Math.sqrt(variance + 1e-5);
  return input.map((value, i) => (value - mean) / divisor * weight[i] + bias[i]);
}
function erf(value) {
  const sign = value < 0 ? -1 : 1;
  const x = Math.abs(value);
  const t = 1 / (1 + 0.3275911 * x);
  return sign * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x));
}
const gelu = value => 0.5 * value * (1 + erf(value / Math.SQRT2));

/** The exported PyTorch architecture: 169→32 projection, four-head self-attention, 64-wide GELU FFN, source logits. */
export function spatialForward(specification, patchRows, mask) {
  const state = specification.spatial.state;
  const vectorRows = patchRows.map(raw => featureVector(specification.additive.features, raw));
  const hidden = vectorRows.map(row => linear(row, state['project.weight'], state['project.bias']));
  const qkv = hidden.map(row => linear(row,
    state['encoder.layers.0.self_attn.in_proj_weight'],
    state['encoder.layers.0.self_attn.in_proj_bias']));
  const attended = hidden.map((_, target) => {
    const joined = [];
    for (let head = 0; head < 4; head++) {
      const offset = head * 8;
      const query = qkv[target].slice(offset, offset + 8);
      const scores = qkv.map(row => {
        const key = row.slice(32 + offset, 40 + offset);
        return query.reduce((sum, value, i) => sum + value * key[i], 0) / Math.sqrt(8);
      });
      const probabilities = softmax(scores);
      for (let i = 0; i < 8; i++) {
        joined.push(qkv.reduce((sum, row, index) => sum + probabilities[index] * row[64 + offset + i], 0));
      }
    }
    return linear(joined, state['encoder.layers.0.self_attn.out_proj.weight'],
      state['encoder.layers.0.self_attn.out_proj.bias']);
  });
  const normalized = hidden.map((row, i) => layerNorm(
    row.map((value, j) => value + attended[i][j]),
    state['encoder.layers.0.norm1.weight'], state['encoder.layers.0.norm1.bias']));
  const outputRows = normalized.map(row => {
    const activated = linear(row, state['encoder.layers.0.linear1.weight'],
      state['encoder.layers.0.linear1.bias']).map(gelu);
    const feedforward = linear(activated, state['encoder.layers.0.linear2.weight'],
      state['encoder.layers.0.linear2.bias']);
    return layerNorm(row.map((value, i) => value + feedforward[i]),
      state['encoder.layers.0.norm2.weight'], state['encoder.layers.0.norm2.bias']);
  });
  const logits = linear(outputRows[0], state['out.weight'], state['out.bias']);
  return { logits, weights: maskedSoftmax(logits, mask), vectorRows, hidden: outputRows[0] };
}

export function dayOfYear(dateString) {
  const date = new Date(`${dateString}T00:00:00Z`);
  if (!Number.isFinite(date.getTime())) throw new Error('Choose a valid date');
  const yearStart = Date.UTC(date.getUTCFullYear(), 0, 1);
  return Math.floor((date.getTime() - yearStart) / 86400000) + 1;
}

/** Change only router context. Calibrated source distributions remain fixed to the saved case. */
export function scenarioRows(demoCase, values, sources) {
  const patch = demoCase.patch_rows.map(row => ({ ...row }));
  const phase = dayOfYear(values.date) * 2 * Math.PI / 365.25;
  for (const row of patch) {
    row['lead.hours'] = values.lead;
    row['season.sin'] = Math.sin(phase);
    row['season.cos'] = Math.cos(phase);
    row['interaction.latitude_season'] = row['geography.latitude'] * row['season.sin'];
  }
  const target = patch[0];
  target['geography.coast'] = values.coast;
  target['geography.elevation'] = values.elevation;
  target['interaction.latitude_season'] = target['geography.latitude'] * target['season.sin'];
  const activeSignals = [];
  for (const source of sources) {
    const input = values.sources[source];
    const active = Boolean(input.available);
    target[`availability.${source}`] = Number(active);
    target[`recent_skill.${source}`] = input.skill;
    target[`age.${source}`] = active ? input.age : 0;
    target[`regime.source_${source}`] = active ? input.signal : 0;
    target[`disagreement.spread_${source}`] = active ? input.spread : 0;
    if (active) activeSignals.push(input.signal);
  }
  if (!activeSignals.length) throw new Error('Keep at least one source available');
  const mean = activeSignals.reduce((a, b) => a + b, 0) / activeSignals.length;
  target['regime.predicted_mean'] = mean;
  target['regime.predicted_max'] = Math.max(...activeSignals);
  target['disagreement.std'] = Math.sqrt(activeSignals.reduce((sum, value) => sum + (value - mean) ** 2, 0) / activeSignals.length);
  target['interaction.lead_regime'] = values.lead * mean / 240;
  return patch;
}

export function mixtureFromFixedSources(demoCase, sources, weights) {
  const receiptSources = new Map(demoCase.base.sources.map(item => [item.source_id, item]));
  const wind = demoCase.variable === 'wind_uv';
  const vector = wind ? [0, 0] : null;
  let mean = 0;
  sources.forEach((source, i) => {
    const value = receiptSources.get(source)?.calibrated_mean;
    if (value === undefined) return;
    if (wind && Array.isArray(value)) { vector[0] += weights[i] * value[0]; vector[1] += weights[i] * value[1]; }
    else if (!wind) mean += weights[i] * value;
  });
  if (wind) mean = Math.hypot(...vector);
  const events = demoCase.base.probabilities.map(event => {
    const contribution = Object.fromEntries(sources.map((source, i) => {
      const baselineWeight = receiptSources.get(source)?.weight || 0;
      const conditional = baselineWeight ? (event.contributions[source] || 0) / baselineWeight : 0;
      return [source, weights[i] * conditional];
    }));
    return { threshold: event.threshold, probability: Object.values(contribution).reduce((a, b) => a + b, 0), contributions: contribution };
  });
  return { mean, vector, events };
}
