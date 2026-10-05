import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { dirname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { GET } from '../api/public-forecast.mjs';
import { GET as searchLocations } from '../api/locations.mjs';
import { GET as chatStatus, POST as chat } from '../api/chat.mjs';
import { GET as intelligence } from '../api/intelligence.mjs';

// Node's env loader preserves variables already set by the deployment environment.
for (const file of ['.env.local', '.env']) {
  try { process.loadEnvFile(resolve(file)); } catch (error) { if (error.code !== 'ENOENT') throw error; }
}

const site = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'site');
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.md': 'text/markdown; charset=utf-8' };
const port = Number(process.env.PORT || 4173);
const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const routes = { '/api/public-forecast':{GET}, '/api/locations':{GET:searchLocations}, '/api/intelligence':{GET:intelligence}, '/api/chat':{GET:chatStatus,POST:chat} };
  if (url.pathname.startsWith('/api/')) {
    try {
      const handler=routes[url.pathname]?.[req.method];
      if(!handler){res.writeHead(routes[url.pathname]?405:404,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'API route or method not found.'}));return;}
      const options={method:req.method,headers:req.headers};
      if(req.method==='POST'){options.body=req;options.duplex='half';}
      const response=await handler(new Request(url,options));
      res.writeHead(response.status,Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch {res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'The local API could not complete this request.'}));}
    return;
  }
  let pathname;
  try { pathname = decodeURIComponent(url.pathname); } catch { res.writeHead(400); res.end(); return; }
  const file = resolve(site, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!file.startsWith(site + sep)) { res.writeHead(403); res.end(); return; }
  try {
    if (!(await stat(file)).isFile()) throw new Error('Not a file');
    const ext = file.slice(file.lastIndexOf('.'));
    res.writeHead(200, { 'Content-Type': types[ext] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404); res.end('Not found');
  }
});
server.listen(port, '127.0.0.1', () => console.log(`Anvaya X preview: http://127.0.0.1:${port}`));
