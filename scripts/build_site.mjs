import { access, readFile, rm } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const site = resolve(root, 'site');
const required = ['index.html', 'app.js', 'analytics.js', 'risk.js', 'workspaces.css', 'gate.js', 'live_view.js', 'weather.js', 'chat.js', 'style.css', 'enhancements.css', 'platform.js', 'platform.css', 'data/india.geojson', 'data/validation-format.json', 'data/demo.json', 'data/gate.json', 'data/catalogue.json', 'data/benchmark.json'];
await Promise.all(required.map(name => access(resolve(site, name))));
await access(resolve(root,'server/data/research-validation.json'));
const html = await readFile(resolve(site, 'index.html'), 'utf8');
if (/ppt\.md|presentation guide/i.test(html)) throw new Error('Private presentation material must not appear in the public site.');
await rm(resolve(site, 'ppt.md'), { force: true });
console.log(`Prepared Vercel site with ${required.length} checked assets; private presentation guide excluded.`);
