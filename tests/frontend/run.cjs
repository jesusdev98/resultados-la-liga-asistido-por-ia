// Source-instrumented deterministic tests; independent of the actual Qwen track.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const home = process.env.LALIGA_TEST_HOME || 'C:/Users/jesus/AppData/Local/Temp/opencode/prueba-deepseek-e2e';
const root = path.resolve(__dirname, '../..');
const web = path.join(root, 'web');
const { createCoverageMap } = require(path.join(home, 'node_modules/istanbul-lib-coverage'));
const report = require(path.join(home, 'node_modules/istanbul-lib-report'));
const reports = require(path.join(home, 'node_modules/istanbul-reports'));
const names = fs.readdirSync(web).filter(name => name.endsWith('.js')).sort();
const snapshot = () => names.map(name => crypto.createHash('sha256').update(fs.readFileSync(path.join(web, name))).digest('hex')).join(':');
const before = snapshot();
const id = new Date().toISOString().replace(/[:.]/g, '-');
const map = createCoverageMap({});
let failed = false;
for (const suite of ['adapters', 'agent', 'app', 'cards', 'structured']) {
  const name = `${id}-${suite}`;
  const result = spawnSync(process.execPath, ['--experimental-vm-modules', '--test', `tests/frontend/${suite}.test.cjs`], {
    cwd: root, encoding: 'utf8', timeout: 120000,
    env: { ...process.env, LALIGA_TEST_HOME: home, LALIGA_COVERAGE_NAME: name },
  });
  console.log(`\n=== ${suite}: exit ${result.status} ===`);
  console.log(result.stdout || '');
  if (result.stderr) console.error(result.stderr);
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) failed = true;
  const filename = path.join(home, 'frontend-coverage', `${name}.json`);
  if (fs.existsSync(filename)) map.merge(JSON.parse(fs.readFileSync(filename, 'utf8')));
  else { console.error(`Missing coverage: ${suite}`); failed = true; }
}
if (snapshot() !== before) { console.error('Source changed during coverage execution.'); failed = true; }
const actual = map.files().map(filename => path.basename(filename)).sort();
if (JSON.stringify(actual) !== JSON.stringify(names)) { console.error('Production source inventory mismatch.', { actual, names }); failed = true; }
const output = path.join(home, 'frontend-coverage', 'runs', id);
const context = report.createContext({ dir: output, coverageMap: map });
for (const type of ['text', 'json', 'json-summary', 'html']) reports.create(type).execute(context);
const summary = map.getCoverageSummary().toJSON();
for (const metric of ['statements', 'branches', 'functions', 'lines']) {
  const value = summary[metric];
  if (value.covered !== value.total || value.skipped) failed = true;
}
console.log(`Original-source frontend coverage: ${output}`);
console.log('DOM and model boundary stubs; this does not establish real-Qwen success.');
process.exitCode = failed ? 1 : 0;
