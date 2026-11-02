const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./harness.cjs');

const MODEL = 'https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.79/+esm';
const logo = 'https://a.espncdn.com/team.png';
const response = (data, ok = true) => ({ ok, status: 503, json: async () => data });
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

async function app(options = {}) {
  const requests = [], agents = [];
  let route = options.route || { intent: 'fixtures', answer: 'routing only' };
  let outcome = options.outcome || { answer: 'Model prose', calls: [] };
  const h = createHarness({
    fetch: async (url, init) => {
      if (url === '/api/laliga/context') return options.dataResponse || response(options.data || {});
      requests.push(JSON.parse(init.body));
      return options.ask ? options.ask(url, init) : response(route);
    },
    modules: {
      './agent.js': {
        runAgent: async args => { agents.push(args); args.onStatus(); return options.agent ? options.agent(args) : outcome; },
        runScopeReply: options.scope || (async () => 'Only La Liga questions.'),
      },
      './webmcp.js': { setupWebMcp: options.setup || (async () => {}) },
      [MODEL]: { CreateMLCEngine: options.engine || (async (_, config) => {
        config.initProgressCallback({ progress: 0.5, text: 'Download' });
        config.initProgressCallback({});
        return { unload: async () => {} };
      }) },
    },
  });
  if (options.gpu !== false) Object.defineProperty(h.window.navigator, 'gpu', { value: {}, configurable: true });
  if (options.before) options.before(h);
  const $ = selector => h.document.querySelector(selector);
  const dispatch = (selector, type) => $(selector).dispatchEvent(new h.window.Event(type, { bubbles: true, cancelable: true }));
  const submit = question => { $('#chat-input').value = question; dispatch('#chat-form', 'submit'); };
  try {
    await h.load('app.js');
    await h.waitFor(() => $('#engine-status').textContent === (options.gpu === false ? 'Error' : 'Sin cargar'));
    return Object.assign(h, { $, requests, agents, dispatch, submit,
      route(value) { route = value; }, outcome(value) { outcome = value; },
      async ready() { dispatch('#load-model', 'click'); await h.waitFor(() => $('#engine-status').textContent === 'Listo'); },
      async send(question) { submit(question); await h.waitFor(() => !$('#send-button').disabled); },
    });
  } catch (error) { h.close(); throw error; }
}

test('recommended Llama is selected without loading until explicitly requested', async () => {
  const loads = [];
  const h = await app({ engine: async model => { loads.push(model); return {}; } });
  try {
    assert.equal(h.$('#model-select').value, 'Llama-3.2-3B-Instruct-q4f32_1-MLC');
    assert.match(h.$('#model-select').selectedOptions[0].textContent, /recomendado/);
    assert.deepEqual(loads, []);
    await h.ready();
    assert.deepEqual(loads, ['Llama-3.2-3B-Instruct-q4f32_1-MLC']);
  } finally { h.close(); }
});

test('saved Qwen choice remains selectable and loads only on request', async () => {
  const loads = [];
  const qwen = 'Qwen2.5-1.5B-Instruct-q4f16_1-MLC';
  const h = await app({ before(h) {
    h.window.localStorage.setItem('prueba-deepseek:model', qwen);
  }, engine: async model => { loads.push(model); return {}; } });
  try {
    assert.equal(h.$('#model-select').value, qwen);
    assert.deepEqual(loads, []);
    await h.ready();
    assert.deepEqual(loads, [qwen]);
  } finally { h.close(); }
});

test('boot, data panel, tabs and unready chat remain independent', async () => {
  const h = await app({ data: {
    today_label: 'Today', updated_at: 'bad date',
    standings: ['Champions', 'Europa', 'Conference', 'Descenso', '', undefined].map((zone, i) => ({ zone, rank: i + 1, team: '<img onerror=alert(1)>', logo: i ? null : logo, played: 2, goal_diff: i - 1, points: 3 })),
    results: [{ home: 'A', away: 'B', home_score: 1, away_score: 0, date: '2026-10-01T12:00:00Z', label: 'FT' }],
    fixtures: [true, false].map(time_valid => ({ home: 'A', away: 'B', date: '2026-10-02T12:00:00Z', time_valid, label: 'fixture' })),
  } });
  try {
    assert.equal(h.$('#tab-standings tbody').children.length, 6);
    assert.equal(h.$('#tab-standings .team-inner span').textContent, '<img onerror=alert(1)>');
    assert.match(h.$('#data-updated').textContent, /bad date/);
    assert.equal(h.document.querySelectorAll('#tab-fixtures li').length, 2);
    h.dispatch('[data-tab="results"]', 'click');
    assert.equal(h.$('#tab-results').hidden, false);
    assert.equal(h.$('[data-tab="results"]').getAttribute('aria-selected'), 'true');
    h.submit('  ');
    assert.equal(h.requests.length, 0);
    await h.send('Question');
    assert.match(h.$('#chat-log').textContent, /Primero cargá/);
    h.dispatch('#clear-chat', 'click');
    assert.equal(h.$('#chat-log').children.length, 1);
    h.dispatch('#model-select', 'change');
  } finally { h.close(); }
});

test('unavailable WebGPU and failing data do not claim model readiness', async () => {
  const h = await app({ gpu: false, dataResponse: response({}, false) });
  try {
    assert.equal(h.$('#load-model').disabled, true);
    assert.equal(h.document.querySelectorAll('.data-error').length, 3);
    h.dispatch('#load-model', 'click');
    await h.send('Question');
    assert.equal(h.requests.length, 0);
  } finally { h.close(); }
});

test('model load handles storage failures, progress, duplicate load and model replacement', async () => {
  const pending = defer(); let loads = 0, unloads = 0, setups = 0;
  const h = await app({ before(h) {
    Object.defineProperty(h.window, 'localStorage', { get() { throw new Error('private'); } });
  }, setup: async () => { setups++; throw new Error('optional WebMCP unavailable'); }, engine: async (_, config) => {
    loads++;
    config.initProgressCallback({ progress: 2, text: 'x'.repeat(100) });
    config.initProgressCallback({ progress: -1 });
    await pending.promise;
    return { unload: async () => { unloads++; throw new Error('unload'); } };
  } });
  try {
    h.dispatch('#load-model', 'click');
    await h.waitFor(() => loads === 1);
    h.dispatch('#load-model', 'click');
    assert.equal(h.$('#progress-bar').style.width, '0%');
    pending.resolve();
    await h.waitFor(() => h.$('#engine-status').textContent === 'Listo');
    h.dispatch('#load-model', 'click');
    assert.equal(loads, 1);
    assert.equal(setups, 1);
    h.$('#model-select').selectedIndex = 1;
    h.dispatch('#model-select', 'change');
    await h.waitFor(() => unloads === 1);
    assert.equal(h.$('#engine-status').textContent, 'Sin cargar');
    await h.ready();
    assert.equal(loads, 2);
  } finally { h.close(); }
});

for (const [error, expected] of [
  [new Error('OOM'), /sin memoria/], [new Error('adapter'), /WebGPU/],
  [new Error('fetch failed'), /descargar/], [new Error('other'), /other/],
  ['string failure', /string failure/], [null, /No se pudo cargar/],
]) test(`model initialization error: ${String(error)}`, async () => {
  const h = await app({ engine: async () => { throw error; } });
  try {
    h.dispatch('#load-model', 'click');
    await h.waitFor(() => h.$('#engine-status').textContent === 'Error');
    assert.match(h.$('#notice').textContent, expected);
    assert.equal(h.$('#load-model').textContent, 'Reintentar');
    assert.equal(h.$('#model-select').disabled, false);
    await h.send('Question'); assert.equal(h.requests.length, 0);
  } finally { h.close(); }
});

test('routing preserves only published verified context and renders multi-card prose atomically', async () => {
  const fixtures = [
    { local: 'A', visitante: 'B', dia: 'Monday', hora: '12:00', horario_confirmado: true, fecha_iso: '2026-10-01' },
    { local: 'B', visitante: 'A', horario_confirmado: false, fecha_iso: '2026-10-02' },
  ];
  const call = { name: 'proximo_partido', ok: true, args: { equipo: 'A', rival: 'B' }, datos: { partidos: fixtures } };
  const h = await app({ route: { intent: 'fixtures', today_label: 'Today', team_logos: { A: logo, B: 'javascript:alert(1)' }, facts: ['verified'] }, outcome: { answer: '<script>alert(1)</script>', calls: [call, { ok: false }] } });
  try {
    await h.ready(); await h.send('next two');
    assert.equal(h.document.querySelectorAll('.bubble-match').length, 2);
    assert.match(h.$('#chat-log').textContent, /Fecha por confirmar/);
    assert.equal(h.$('.bubble-model script'), null);
    assert.equal(h.$('.bubble-model .bubble-text').textContent, '<script>alert(1)</script>');
    assert.equal(h.document.querySelectorAll('#chat-log img').length, 3);
    for (const img of [...h.document.querySelectorAll('#chat-log img')]) img.dispatchEvent(new h.window.Event('error'));
    assert.equal(h.document.querySelectorAll('#chat-log img').length, 0);
    h.outcome({ answer: '', calls: [call] }); await h.send('failed prose');
    assert.equal(h.document.querySelectorAll('.bubble-match').length, 2);
    assert.match(h.$('#chat-log').textContent, /redactar una respuesta fiable/);
    h.outcome({ answer: 'Confirmed', calls: [call] }); await h.send('sure?');
    assert.equal(h.requests[2].previous_question, 'next two');
    assert.equal(h.requests[2].fixture_context.despues_de, '2026-10-02');
    assert.equal(h.agents[2].context.previousQuestion, 'next two');
    h.route({ intent: 'none' }); await h.send('off topic');
    h.route({ intent: 'fixtures' }); await h.send('new question');
    assert.equal(h.requests.at(-1).previous_question, null);
    assert.equal(h.requests.at(-1).fixture_context, null);
    assert.equal(h.agents.at(-1).context.previousTool, null);
    for (let i = 0; i < 4; i++) await h.send(`repeat ${i}`);
    h.dispatch('#clear-chat', 'click'); await h.send('after clear');
    assert.equal(h.requests.at(-1).previous_question, null);
  } finally { h.close(); }
});

test('result cards, fallback resources and logo allowlist reject unsafe sources', async () => {
  const h = await app({ route: { intent: 'result', team_logos: { A: logo, B: 'https://user:pass@a.espncdn.com/team', C: 7, D: 'invalid', E: 'https://a.espncdn.com:8443/team', F: 'http://a.espncdn.com/team', G: 'https://evil.example/team' } }, outcome: { answer: 'Result', calls: [
    { name: 'ultimo_resultado', ok: true, datos: { local: 'A', visitante: 'B', goles_local: 2, goles_visitante: 1, dia: 'Today' } },
    { name: 'ultimo_resultado', ok: true, datos: { goles_local: 0, goles_visitante: 0 } },
    { name: 'table', ok: true, content: [] },
    { name: 'table', ok: true, content: [{ type: 'resource', resource: { uri: 'ui://prueba-deepseek/panel', mimeType: 'text/html', text: '<b>Table</b>' } }] },
  ] } });
  try {
    await h.ready(); await h.send('result');
    assert.equal(h.document.querySelectorAll('.result-card-score').length, 2);
    assert.match(h.$('#chat-log').textContent, /Fecha no disponible/);
    assert.equal(h.document.querySelectorAll('.bubble-mcp-ui').length, 1);
    for (const image of [...h.document.querySelectorAll('#chat-log img')]) image.dispatchEvent(new h.window.Event('error'));
    assert.equal(h.document.querySelectorAll('#chat-log img').length, 0);
  } finally { h.close(); }
});

test('agent exceptions and malformed outcomes fail closed without publishing tools', async () => {
  let index = 0;
  const h = await app({ agent: async () => {
    if (index++ === 0) throw new Error('agent failed');
    return { answer: null, calls: {} };
  } });
  try {
    await h.ready(); await h.send('first'); await h.send('second');
    assert.equal(h.document.querySelectorAll('.bubble-match, .bubble-model').length, 0);
    assert.match(h.$('#chat-log').textContent, /no pudo consultar/);
  } finally { h.close(); }
});

test('scope routing and HTTP failures restore controls without exposing resolver answers', async () => {
  let fail = true;
  const h = await app({ ask: async () => fail ? response({}, false) : response({ intent: 'unknown_football', answer: 'SECRET RESOLVER' }), scope: async () => '' });
  try {
    await h.ready(); await h.send('first');
    assert.equal(h.$('#notice').classList.contains('error'), true);
    fail = false; await h.send('unsupported');
    assert.match(h.$('#chat-log').textContent, /No pude generar/);
    assert.doesNotMatch(h.$('#chat-log').textContent, /SECRET RESOLVER/);
  } finally { h.close(); }
});

test('busy submit and clear are ignored while a resolver request is pending', async () => {
  const pending = defer();
  const h = await app({ ask: async () => pending.promise });
  try {
    await h.ready(); h.submit('first'); h.submit('second'); h.dispatch('#clear-chat', 'click');
    assert.equal(h.requests.length, 1);
    assert.match(h.$('#chat-log').textContent, /first/);
    pending.resolve(response({ intent: 'fixtures' }));
    await h.waitFor(() => !h.$('#send-button').disabled);
  } finally { h.close(); }
});

test('unknown saved model, absent panel data and empty selection use safe defaults', async () => {
  let selected;
  const h = await app({ dataResponse: response({}, false), before(h) {
    h.window.localStorage.setItem('prueba-deepseek:model', 'removed-model');
    const tab = h.document.createElement('button');
    tab.className = 'tab'; tab.dataset.tab = 'missing';
    h.document.body.appendChild(tab);
  }, engine: async model => { selected = model; return {}; } });
  try {
    h.dispatch('[data-tab="missing"]', 'click');
    assert.equal(h.$('#tab-standings').hidden, true);
    h.$('#model-select').value = '';
    await h.ready();
    assert.equal(selected, 'Llama-3.2-3B-Instruct-q4f32_1-MLC');
    h.dispatch('#model-select', 'change');
    assert.equal(h.$('#engine-status').textContent, 'Sin cargar');
  } finally { h.close(); }
});

test('published answers without successful calls retain resolver fixture context only when verified', async () => {
  const h = await app({ route: { intent: 'fixtures', answer: 'verified routing', fixture_context: { equipo: 'A' }, team_logos: null }, outcome: { answer: 'Model explanation', calls: [] } });
  try {
    await h.ready(); await h.send('first'); await h.send('followup');
    assert.equal(h.requests[1].fixture_context.equipo, 'A');
    h.route({ intent: 'fixtures' }); await h.send('unverified'); await h.send('again');
    assert.equal(h.requests.at(-1).previous_question, 'followup');
    h.route({ intent: 'fixtures', answer: 'verified' }); await h.send('reset fixture'); await h.send('next');
    assert.equal(h.requests.at(-1).fixture_context, null);
    h.route({}); await h.send('implicit unsupported');
    assert.match(h.$('#chat-log').textContent, /Only La Liga/);
  } finally { h.close(); }
});

for (const intent of ['fixtures', 'none']) test(`model replacement during routing fails closed: ${intent}`, async () => {
  const pending = defer();
  const h = await app({ ask: async () => pending.promise });
  try {
    await h.ready(); h.submit('pending');
    h.$('#model-select').selectedIndex = 1;
    h.dispatch('#model-select', 'change');
    pending.resolve(response({ intent }));
    await h.waitFor(() => !h.$('#send-button').disabled);
    assert.equal(h.agents.length, 0);
    assert.match(h.$('#chat-log').textContent, intent === 'none' ? /no hay modelo cargado/ : /Primero cargá/);
  } finally { h.close(); }
});

test('malformed fixture entries are skipped and result context clears fixture cursor', async () => {
  const h = await app({ outcome: { answer: 'Fixtures', calls: [{ name: 'proximo_partido', ok: true, args: {}, datos: { partidos: [{ local: 'A' }, null, 'invalid'] } }] } });
  try {
    await h.ready(); await h.send('fixtures');
    assert.equal(h.document.querySelectorAll('.bubble-match').length, 1);
    h.outcome({ answer: 'Result', calls: [{ name: 'ultimo_resultado', ok: true, args: {}, datos: { goles_local: 0, goles_visitante: 0 } }] });
    await h.send('result'); await h.send('followup');
    assert.equal(h.requests.at(-1).fixture_context, null);
  } finally { h.close(); }
});
