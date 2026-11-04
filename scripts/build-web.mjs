import { build } from 'esbuild';
import {resolve,relative,isAbsolute} from 'node:path';
import {rm} from 'node:fs/promises';
const outdir=resolve(process.env.FORMFORGE_WEB_OUTPUT||'web/assets');
const inside=relative(process.cwd(),outdir);
if(!inside||inside.startsWith('..')||isAbsolute(inside))throw new Error('Web output must remain in a child directory of FormForge.');
await rm(outdir,{recursive:true,force:true});
await build({ entryPoints: ['web/src/app.ts'], bundle: true, format: 'esm', target: 'es2022',
  outdir, minify: true, sourcemap: true, splitting: true });
console.log('FormForge web build complete');
