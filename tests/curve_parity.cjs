/** Evaluate actual frontend curves for comparison with Python in test_curve_parity.py. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const content = fs.readFileSync(path.join(__dirname, '..', 'web', 'curve.js'), 'utf8');
const src = content
  .replace("import { app } from '../../scripts/app.js';", '')
  .replace('import.meta.url', "'file:///tmp/curve.js'")
  + '\n globalThis.nmEvaluate = evaluate;';
const doc = { getElementById: () => ({ tagName: 'link' }), head: {} };
const sandbox = { document: doc, app: { registerExtension() {} }, URL };
vm.runInNewContext(src, sandbox);
const batches = JSON.parse(fs.readFileSync(0, 'utf8'));
const values = batches.map(({ points, interpolation, positions }) =>
  positions.map(x => sandbox.nmEvaluate(points, interpolation, x)));
process.stdout.write(JSON.stringify(values));
