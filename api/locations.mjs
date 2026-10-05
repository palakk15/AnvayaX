import { searchLocations } from '../server/locations.mjs';
import { errorResponse } from '../server/http.mjs';
export { normalizeLocations } from '../server/locations.mjs';
export async function GET(request) {
  try { return Response.json(await searchLocations(new URL(request.url).searchParams.get('q')), {headers:{'Cache-Control':'public, s-maxage=86400'}}); }
  catch(error) { return errorResponse(error); }
}
