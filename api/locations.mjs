const endpoint = 'https://geocoding-api.open-meteo.com/v1/search';

export function normalizeLocations(data) {
  if (!Array.isArray(data?.results)) return [];
  const seen = new Set();
  return data.results.filter(place => {
    if (place.country_code !== 'IN' || !Number.isInteger(place.id) || seen.has(place.id)) return false;
    if (!Number.isFinite(place.latitude) || !Number.isFinite(place.longitude)) return false;
    seen.add(place.id);
    return true;
  }).slice(0, 10).map(place => ({
    id: place.id, name: String(place.name || '').slice(0, 100),
    district: String(place.admin2 || '').slice(0, 100),
    state: String(place.admin1 || '').slice(0, 100),
    country_code: 'IN', latitude: place.latitude, longitude: place.longitude,
    timezone: place.timezone || 'Asia/Kolkata',
  }));
}

export async function GET(request) {
  const query = (new URL(request.url).searchParams.get('q') || '').trim();
  if (query.length < 2 || query.length > 70 || /[\x00-\x1f<>]/.test(query)) {
    return Response.json({ error: 'Enter 2–70 characters for an Indian place or PIN code.' }, { status: 400 });
  }
  const url = new URL(endpoint);
  Object.entries({ name: query, count: 10, language: 'en', countryCode: 'IN', format: 'json' })
    .forEach(([key, value]) => url.searchParams.set(key, String(value)));
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(5000), redirect: 'manual', headers: { Accept: 'application/json' } });
    if (!response.ok || Number(response.headers.get('content-length') || 0) > 200000) throw Error('Location provider unavailable');
    const raw = await response.arrayBuffer();
    if (raw.byteLength > 200000) throw Error('Location response too large');
    const locations = normalizeLocations(JSON.parse(Buffer.from(raw).toString('utf8')));
    return Response.json({ locations, query, source: 'Open-Meteo Geocoding', documentation_url: 'https://open-meteo.com/en/docs/geocoding-api' },
      { headers: { 'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=3600' } });
  } catch (error) {
    return Response.json({ error: 'Location search is temporarily unavailable.' }, { status: 502, headers: { 'Cache-Control': 'no-store' } });
  }
}

export default GET;
