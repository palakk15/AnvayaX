import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = resolve(root, 'ppt.md');
const destination = resolve(root, 'site', 'ppt.md');
const guide = await readFile(source, 'utf8');
if (!guide.includes('BharatBlend') || guide.length < 10000) throw new Error('Presentation guide is missing or incomplete.');
await mkdir(resolve(root, 'site'), { recursive: true });
await copyFile(source, destination);
console.log(`Prepared Vercel site; presentation guide ${guide.length} characters.`);
