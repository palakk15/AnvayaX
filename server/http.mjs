/** Bounded JSON transport and warm-instance caching shared by all providers. */
export class ServiceError extends Error {
  constructor(message, status = 502) { super(message); this.status = status; }
}
export async function fetchJSON(url, { timeout = 10000, maxBytes = 2_000_000, ...options } = {}) {
  let response;
  try { response = await fetch(url, { ...options, redirect: 'error', signal: AbortSignal.timeout(timeout), headers: { Accept: 'application/json', ...options.headers } }); }
  catch { throw new ServiceError('The data provider could not be reached. Please retry shortly.'); }
  if (!response.ok) throw new ServiceError(response.status === 429 ? 'The data provider is rate-limiting requests. Please wait a few minutes.' : `The data provider returned HTTP ${response.status}.`, response.status === 429 ? 429 : 502);
  if (Number(response.headers.get('content-length')) > maxBytes) throw new ServiceError('The provider response was too large.');
  const reader = response.body.getReader(); const chunks = []; let size = 0;
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    size += value.length;
    if (size > maxBytes) { await reader.cancel(); throw new ServiceError('The provider response was too large.'); }
    chunks.push(value);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new ServiceError('The data provider returned an unreadable response. Please retry shortly.'); }
}
const cache = new Map(), pending = new Map();
export async function cached(key, ttl, loader) {
  const entry = cache.get(key);
  if (entry && entry.until > Date.now()) return entry.value;
  if (pending.has(key)) return pending.get(key);
  const promise = loader().then(value => {
    if (cache.size >= 300) cache.delete(cache.keys().next().value);
    cache.set(key, { value, until: Date.now() + ttl }); return value;
  }).finally(() => pending.delete(key));
  pending.set(key, promise); return promise;
}
export function clearCache() { cache.clear(); pending.clear(); }
export function errorResponse(error) {
  return Response.json({ error: error instanceof ServiceError ? error.message : 'This service is temporarily unavailable. Please try again.' }, { status: error.status || 502, headers: { 'Cache-Control': 'no-store' } });
}
