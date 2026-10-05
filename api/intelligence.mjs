import {resolveLocation} from '../server/locations.mjs';
import {getIntelligence} from '../server/intelligence.mjs';
import {skillReport} from '../server/skill-store.mjs';
import {getEnsemble} from '../server/ensemble.mjs';
import {errorResponse,ServiceError} from '../server/http.mjs';
export async function GET(request) {
  try {
    const params=new URL(request.url).searchParams,action=params.get('action')||'live';
    if(!['live','skill','ensemble'].includes(action))throw new ServiceError('Unknown intelligence action',400);
    if(action==='skill'&&(!['real','research'].includes(params.get('mode')||'real')||!['temperature','rainfall','wind_uv'].includes(params.get('variable')||'temperature')||![7,30,90].includes(Number(params.get('recent')||30))))throw new ServiceError('Invalid evidence filters',400);
    const result=action==='skill'?await skillReport(params):await (action==='ensemble'?getEnsemble:getIntelligence)(await resolveLocation(params));
    return Response.json(result,{headers:{'Cache-Control':'public, s-maxage=300'}});
  } catch(e){return errorResponse(e);}
}
