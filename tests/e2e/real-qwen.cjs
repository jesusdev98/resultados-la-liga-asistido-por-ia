/* Real Chrome/WebGPU local-model integration. No model or HTTP response substitution. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { verifyCase } = require('./verify-evidence.cjs');
const { createRequire } = require('node:module');
const home = process.env.LALIGA_TEST_HOME;
if (!home) throw new Error('Set LALIGA_TEST_HOME to an external test dependency/artifact directory.');
const external = createRequire(path.join(home, 'package.json'));
const { chromium } = external('playwright');
const base = process.env.LALIGA_BASE_URL || 'http://localhost:8000';
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const output = path.join(home, 'runs', stamp);
fs.mkdirSync(output, { recursive: true });
const save = (name, data) => fs.writeFileSync(path.join(output, name), JSON.stringify(data, null, 2));
const matrix = [
  ['atleti-three', 'dime los 3 proximos partidos del atleti', 3],
  ['atletico-three-worded', 'si los 3 proximos partidos que jugara el atletico de madrid', 3],
  ['madrid-three', 'vale pues dime los 3 proximos partidos del madrid', 3],
  ['madrid-one', 'cual es el proximo partido del madrid', 1],
  ['barca-one', 'cuando juega el barca', 1],
  ['atleti-one', 'cuando juega el atleti', 1],
  ['madrid-two', 'dime los dos proximos partidos del madrid', 2],
  ['atleti-two', 'dime los 2 proximos partidos del atleti', 2],
  ['barca-three', 'los tres proximos partidos del barca', 3],
  ['sevilla-three', 'los 3 proximos partidos del sevilla', 3],
  ['valencia-three', 'los tres proximos partidos del valencia', 3],
  ['following-one', 'y el siguiente a ese?', 1, 'cual es el proximo partido del madrid'],
  ['following-three-numeric', 'vale y los 3 siguientes?', 3, 'cuando juega el madrid'],
  ['following-three-worded', 'y los tres siguientes partidos?', 3, 'cuando juega el atleti'],
  ['next-plus-three', 'vale y el siguiente a ese? y despues de eso los 3 siguientes?', 4, 'cuando juega el madrid'],
  ['after-date', 'cual es el partido del madrid despues del 1 de noviembre de 2026', 1],
  ['after-event', 'y despues de ese partido cuando vuelve a jugar el madrid?', 1, 'cuando juega el madrid'],
  ['madrid-result', 'como quedo el madrid', 'ultimo_resultado'],
  ['clasico-result', 'y cuanto ha quedado el madrid barsa?', 'ultimo_resultado'],
  ['atleti-result', 'cual fue el ultimo resultado del atleti', 'ultimo_resultado'],
  ['table', 'dime la tabla', 'tabla'],
  ['standing', 'en que puesto esta el madrid', 'posicion'],
  ['bottom', 'quienes estan en descenso', 'descenso'],
  ['ballon-dor', 'dime como quedo el balon de oro', 'scope'],
  ['unrelated', 'cual es la capital de Francia', 'scope'],
  ['unsupported-player', 'cuantos balones de oro tiene Messi', 'scope'],
  ['unsupported-stadium', 'en que estadio juega ese partido?', 'scope', 'cuando juega el madrid'],
  ['following-list', 'y los tres siguientes?', 3, 'los tres proximos partidos del madrid'],
  ['confirmation', 'seguro?', 1, 'cuando juega el madrid'],
  ['confirmation-after-next', 'seguro?', 1, 'cuando juega el madrid', 'y el siguiente a ese?'],
  ['following-three-exact-user', 'y los 3 proximos despues de ese del madrid?', 3,
    'como quedo el madrid', 'vale y el proximo partido cuando es?'],
];

(async () => {
  let context;
  const report = { base, model: process.env.LALIGA_MODEL || 'Llama-3.2-3B-Instruct-q4f32_1-MLC', modelMocked: false,
    cases: [], preflight: {}, coverage: null };
  const budgetMs = Number(process.env.LALIGA_RUN_TIMEOUT_MS) || 20 * 60 * 1000;
  const watchdog = setTimeout(() => {
    report.interrupted = 'run_budget_exhausted';
    save('results.json', report);
    console.error(`BLOCKED run exceeded ${budgetMs}ms; closing isolated browser`);
    if (context) void context.close().catch(() => {});
    setTimeout(() => process.exit(2), 5000).unref();
  }, budgetMs);
  watchdog.unref();
  console.log(`ARTIFACTS ${output}`);
  try {
    context = await chromium.launchPersistentContext(path.join(home, 'isolated-chrome-profile'), {
      channel: 'chrome', headless: false, chromiumSandbox: true,
      viewport: { width: 1440, height: 1000 }, locale: 'es-ES', timezoneId: 'Europe/Madrid',
    });
    const page = context.pages()[0] || await context.newPage();
    const diagnostics = [];
    const network = [];
    await page.exposeFunction('__recordLaligaDiagnostic', event => diagnostics.push(event));
    await page.addInitScript(() => {
      globalThis.__LALIGA_DIAGNOSTICS__ = event => { void globalThis.__recordLaligaDiagnostic(event); };
    });
    page.on('response', async response => {
      if (response.url().startsWith(`${base}/api/`)) {
        try { network.push({ url: new URL(response.url()).pathname,
          request: response.request().postDataJSON(), response: await response.json() }); } catch { /* Not JSON. */ }
      }
    });
    page.on('console', msg => {
      if (msg.type() === 'error' || msg.type() === 'warning') {
        fs.appendFileSync(path.join(output, 'console.jsonl'), JSON.stringify({ type: msg.type(), text: msg.text().slice(0, 2000) }) + '\n');
      }
    });
    page.on('pageerror', error => {
      fs.appendFileSync(path.join(output, 'page-errors.jsonl'), JSON.stringify({ message: error.message }) + '\n');
    });
    await page.coverage.startJSCoverage();
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60000 });
    report.preflight = await page.evaluate(async () => {
      const adapter = await navigator.gpu?.requestAdapter();
      return { secure: isSecureContext, webgpu: !!navigator.gpu, adapter: !!adapter,
        gpu: adapter?.info?.description || adapter?.info?.vendor || null };
    });
    assert.equal(report.preflight.adapter, true, 'A real WebGPU adapter is required.');
    const preloadedRequests = network.length;
    await page.locator('#chat-input').fill('dime los 3 proximos partidos del atleti');
    await page.locator('#send-button').click();
    assert.match(await page.locator('#chat-log').innerText(), /carg.*modelo/i);
    assert.equal(network.length, preloadedRequests, 'Unloaded submission must not query data.');
    report.preflight.preloadGuard = true;
    await page.locator('#model-select').selectOption(report.model);
    const start = Date.now();
    await page.locator('#load-model').click();
    console.log(`MODEL_LOADING real ${report.model}; download may be required`);
    await page.waitForFunction(() => document.querySelector('#load-model')?.textContent.includes('Modelo cargado') ||
      document.querySelector('#engine-status')?.textContent === 'Error', null, { timeout: 900000 });
    assert.match(await page.locator('#load-model').innerText(), /Modelo cargado/);
    report.preflight.modelLoadMs = Date.now() - start;
    console.log(`MODEL_READY ${report.preflight.modelLoadMs}ms`);

    async function turn(question) {
      const begin = diagnostics.length;
      const apiStart = network.length;
      const bubbles = await page.locator('#chat-log > article').count();
      const started = Date.now();
      await page.locator('#chat-input').fill(question);
      await page.locator('#send-button').click();
      await page.waitForFunction(previous => {
        const input = document.querySelector('#chat-input');
        return !input.disabled && document.querySelectorAll('#chat-log > article').length > previous;
      }, bubbles, { timeout: 240000 });
      const newText = await page.locator('#chat-log > article').allTextContents();
      return { question, elapsedMs: Date.now() - started, diagnostics: diagnostics.slice(begin),
        network: network.slice(apiStart), text: newText.slice(bubbles),
        cards: await page.locator('#chat-log .bubble-match').count() };
    }
    const requested = process.env.LALIGA_CASE_IDS?.split(',').map(id => id.trim()).filter(Boolean);
    const selected = requested ? matrix.filter(item => requested.includes(item[0])) : matrix;
    assert.ok(selected.length && (!requested || selected.length === requested.length), 'Unknown or duplicate case IDs.');
    const limit = Number(process.env.LALIGA_CASE_LIMIT || selected.length);
    for (const [id, question, expected, setup, secondSetup] of selected.slice(0, limit)) {
      const item = { id, question, expected, status: 'failed' };
      console.log(`CASE_START ${report.cases.length + 1}/${Math.min(limit, selected.length)} ${id}`);
      try {
        await page.locator('#clear-chat').click();
        for (const prerequisite of [setup, secondSetup].filter(Boolean)) {
          const pre = await turn(prerequisite);
          (item.prerequisites ||= []).push(pre);
          assert.ok(!pre.text.join(' ').includes('no pudo redactar'), 'Prerequisite narration failed.');
          assert.ok(pre.network.some(call => call.url === '/api/tool' && call.response.ok), 'Prerequisite did not execute a verified tool.');
        }
        const beforeCards = await page.locator('#chat-log .bubble-match').count();
        item.observed = await turn(question);
        const tools = item.observed.network.filter(n => n.url === '/api/tool');
        const published = item.observed.text.join(' ');
        assert.ok(!/no pudo redactar|no pudo consultar|No pude generar/i.test(published), 'Model did not publish a usable answer.');
        if (expected === 'scope') {
          assert.equal(tools.length, 0, 'Unsupported question must not run a match tool.');
          assert.match(published, /no tengo (?:datos|informaci.n) verificada?/i);
        } else {
          const successful = tools.filter(t => t.response.ok);
          assert.equal(successful.length, 1, 'Exactly one successful model-selected tool expected.');
          const tool = successful[0];
          assert.equal(tool.request.name, typeof expected === 'number' ? 'proximo_partido' : expected);
          if (typeof expected === 'number') {
            const matches = tool.response.datos.partidos;
            assert.equal(matches.length, expected, 'Requested fixture count was not returned.');
            assert.equal(item.observed.cards - beforeCards, expected, 'All requested cards must appear.');
            for (const match of matches) {
              assert.ok(published.includes(match.local) && published.includes(match.visitante) && published.includes(match.dia));
            }
            const corrections = item.observed.diagnostics.filter(d => d.stage === 'writer_correction');
            assert.ok(corrections.length <= 1, 'At most one targeted narration correction per turn.');
            assert.equal(item.observed.diagnostics.filter(d => d.stage === 'writer').length, expected + corrections.length);
          }
        }
        verifyCase(item);
        item.status = 'passed';
      } catch (error) {
        item.error = error.message;
        await page.screenshot({ path: path.join(output, `${id}.png`), fullPage: true }).catch(() => {});
      }
      report.cases.push(item);
      save('results.json', report);
      console.log(`${item.status.toUpperCase()} ${id}${item.error ? ': ' + item.error : ''}`);
      if (report.interrupted) break;
    }
    const entries = (await page.coverage.stopJSCoverage()).filter(e => e.url.startsWith(base) && e.url.endsWith('.js'));
    save('browser-v8-coverage.json', entries);
    const expectedSources = fs.readdirSync(path.resolve(__dirname, '../../web')).filter(name => name.endsWith('.js')).sort();
    const loadedSources = [...new Set(entries.map(entry => path.posix.basename(new URL(entry.url).pathname)))].sort();
    report.coverageInventory = { expected: expectedSources, loaded: loadedSources, kind: 'browser-v8-converted' };
    assert.deepEqual(loadedSources, expectedSources, 'Browser coverage must include every production JavaScript module.');
    const coverageMap = external('istanbul-lib-coverage').createCoverageMap({});
    for (const entry of entries) {
      const converter = external('v8-to-istanbul')(new URL(entry.url).pathname, 0, { source: entry.source });
      await converter.load(); converter.applyCoverage(entry.functions); coverageMap.merge(converter.toIstanbul());
    }
    const libReport = external('istanbul-lib-report');
    const reporter = libReport.createContext({ dir: path.join(output, 'coverage'), coverageMap });
    external('istanbul-reports').create('json-summary').execute(reporter);
    external('istanbul-reports').create('html').execute(reporter);
    report.coverage = JSON.parse(fs.readFileSync(path.join(output, 'coverage', 'coverage-summary.json'), 'utf8'));
    report.summary = { passed: report.cases.filter(c => c.status === 'passed').length,
      failed: report.cases.filter(c => c.status === 'failed').length, total: report.cases.length };
    save('results.json', report);
    console.log(`SUMMARY ${JSON.stringify(report.summary)}`);
    process.exitCode = report.summary.failed ? 1 : 0;
  } catch (error) {
    report.blocked = error.message; save('results.json', report);
    console.error(`BLOCKED ${error.message}`); process.exitCode = 2;
  } finally { clearTimeout(watchdog); if (context) await context.close(); }
})();
