import { cached, fetchJSON, ServiceError } from './http.mjs';
export const cities = Object.freeze({ Delhi: [28.6139,77.209], Mumbai: [19.076,72.8777], Bengaluru: [12.9716,77.5946], Chennai: [13.0827,80.2707], Kolkata: [22.5726,88.3639], Hyderabad: [17.385,78.4867], Guwahati: [26.1445,91.7362], Jaipur: [26.9124,75.7873] });
export function normalizeLocations(data) {
  const seen = new Set();
  return (Array.isArray(data?.results) ? data.results : []).filter(p => {
    if (p.country_code !== 'IN' || !Number.isInteger(p.id) || seen.has(p.id) || !Number.isFinite(p.latitude) || !Number.isFinite(p.longitude)) return false;
    seen.add(p.id); return true;
  }).slice(0,20).map(p => ({ id:p.id, name:String(p.name || '').slice(0,100), district:String(p.admin2 || '').slice(0,100), state:String(p.admin1 || '').slice(0,100), latitude:p.latitude, longitude:p.longitude, country_code:'IN', timezone:'Asia/Kolkata', precision:'place', source:'Open-Meteo / GeoNames' }));
}
async function gazetteer(query) {
  const url = new URL('https://geocoding-api.open-meteo.com/v1/search');
  Object.entries({ name:query, count:30, language:'en', countryCode:'IN', format:'json' }).forEach(([k,v]) => url.searchParams.set(k,v));
  return normalizeLocations(await fetchJSON(url, { maxBytes:300000 }));
}
export async function searchLocations(query) {
  query = String(query || '').trim();
  if (query.length < 2 || query.length > 100 || /[\x00-\x1f<>]/.test(query)) throw new ServiceError('Enter a town, district, or six-digit Indian PIN code.',400);
  return cached(`location:${query.toLowerCase()}`,86400000,async () => {
    if (/^\d+$/.test(query)) {
      if (!/^[1-9]\d{5}$/.test(query)) throw new ServiceError('An Indian PIN code has six digits.',400);
      // Postal records contain names, not coordinates. Explicitly label district approximations.
      const direct = await gazetteer(query).catch(() => []);
      if (direct.length) return { locations:direct.map(p=>({...p,pincode:query,precision:'postal gazetteer'})), source:'Open-Meteo / GeoNames' };
      const postal = await fetchJSON(`https://api.postalpincode.in/pincode/${query}`, { maxBytes:500000 });
      const offices = postal?.[0]?.PostOffice;
      if (!Array.isArray(offices) || !offices.length) return { locations:[], message:'No postal record found. Search your town or use coordinates instead.' };
      const areas = [...new Map(offices.map(o=>[`${o.District}|${o.State}`,o])).values()].slice(0,3);
      const groups = await Promise.all(areas.map(async office => {
        const candidates = await gazetteer(office.District);
        const clean = s => String(s).toLowerCase().replace(/[^a-z]/g,'');
        // Prefix search also finds unrelated names such as Punegaon for Pune.
        // A district approximation must resolve the actual district name, not those prefixes.
        return candidates.filter(p=>clean(p.state)===clean(office.State) && clean(p.name)===clean(office.District)).slice(0,1).map(p=>({...p,pincode:query,precision:'district approximation', postal_offices:offices.map(o=>o.Name).slice(0,20), source:'Postal PIN Code directory + Open-Meteo / GeoNames', note:`PIN ${query}: coordinates represent ${p.name}, not the exact postal boundary. Use your location or coordinates for a more precise forecast.`}));
      }));
      return { locations:groups.flat(), message:'Postal search uses approximate district coordinates. Confirm the place before viewing.', source:'Postal PIN Code directory (third party); Open-Meteo / GeoNames' };
    }
    return { locations:await gazetteer(query), source:'Open-Meteo / GeoNames' };
  });
}
export async function resolveLocation(params) {
  if (params.has('latitude') || params.has('longitude')) {
    const latText = params.get('latitude'), lonText = params.get('longitude');
    const latitude = Number(latText), longitude = Number(lonText);
    if (!latText?.trim() || !lonText?.trim() || !Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < 6 || latitude > 38 || longitude < 68 || longitude > 98) throw new ServiceError('Enter coordinates in the India region (6–38° N, 68–98° E), including the islands.',400);
    return { name:`${latitude.toFixed(3)}° N, ${longitude.toFixed(3)}° E`, latitude, longitude, precision:'user coordinates', note:'Coordinates supplied by you. The regional bounds do not verify a national boundary.' };
  }
  if (params.has('place_id')) {
    const id = params.get('place_id');
    if (!/^\d{1,12}$/.test(id)) throw new ServiceError('Choose a valid Indian location.',400);
    return cached(`place:${id}`,86400000,async()=>{
      const data = await fetchJSON(`https://geocoding-api.open-meteo.com/v1/get?id=${id}`, {maxBytes:50000});
      const place = normalizeLocations({results:[data]})[0];
      if (!place) throw new ServiceError('Choose a location in India.',400);
      return place;
    });
  }
  const city = params.get('city')?.trim();
  if(!city)throw new ServiceError('Choose a city, search for a place, or enter coordinates first.',400);
  if (Object.hasOwn(cities,city)) { const [latitude,longitude]=cities[city]; return {name:city,latitude,longitude,country_code:'IN',precision:'city centre'}; }
  const result = await searchLocations(city);
  if (result.locations.length !== 1) throw new ServiceError(result.locations.length ? 'Several places match. Choose a search result first.' : 'Place not found. Try a district name or coordinates.',400);
  return result.locations[0];
}
