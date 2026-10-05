/** Presentation utilities for real provider forecasts. All timestamps are UTC internally. */
export function futureTargetTimes(rows, now = Date.now(), limit = 24) {
  return [...new Set(rows.map(row => row.valid_time))]
    .filter(time => Number.isFinite(Date.parse(time)) && Date.parse(time) > now)
    .sort((a, b) => Date.parse(a) - Date.parse(b))
    .slice(0, limit);
}

export function formatIST(value, { date = true, time = true } = {}) {
  const options = { timeZone: 'Asia/Kolkata', hourCycle: 'h23' };
  if (date) Object.assign(options, { day: 'numeric', month: 'short' });
  if (time) Object.assign(options, { hour: '2-digit', minute: '2-digit' });
  return `${new Intl.DateTimeFormat('en-IN', options).format(new Date(value))} IST`;
}

export function modelWindow(rows, model, variable, times) {
  const lookup = new Map(rows.filter(row => row.model === model).map(row => [row.valid_time, row]));
  const points = times.map(time => ({ time, value: lookup.get(time)?.[variable] ?? null }));
  const valid = points.filter(point => Number.isFinite(point.value));
  if (!valid.length) return { points, count: 0, sum: null, min: null, max: null, maxTime: null };
  const peak = valid.reduce((best, point) => point.value > best.value ? point : best);
  return { points, count: valid.length, sum: valid.reduce((total, point) => total + point.value, 0),
    min: Math.min(...valid.map(point => point.value)), max: peak.value, maxTime: peak.time };
}
