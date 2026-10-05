import { getForecast } from '../server/forecast.mjs';
import { resolveLocation } from '../server/locations.mjs';
import { errorResponse } from '../server/http.mjs';
export { cities } from '../server/locations.mjs';
export { models, normalizeProvider } from '../server/forecast.mjs';
export async function GET(request) {
  try {
    const location = await resolveLocation(new URL(request.url).searchParams);
    return Response.json(await getForecast(location), {headers:{'Cache-Control':'public, s-maxage=300, stale-while-revalidate=60'}});
  } catch(error) { return errorResponse(error); }
}
