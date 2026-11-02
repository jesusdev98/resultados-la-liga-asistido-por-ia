const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./harness.cjs');
const resource = { type: 'resource', resource: { uri: 'ui://prueba-deepseek/panel', mimeType: 'text/html', text: '<h1>Source panel</h1>' } };

test('resource admission rejects malformed data and renders only the first valid sandbox', async () => {
  const h = createHarness();
  try {
    const api = await h.load('mcp-ui.js');
    const container = h.document.createElement('div');
    h.document.body.append(container);
    const bad = [null, {}, { type: 'text' }, { ...resource, resource: null },
      { ...resource, resource: { ...resource.resource, uri: 'https://untrusted.invalid' } },
      { ...resource, resource: { ...resource.resource, mimeType: 'application/javascript' } },
      { ...resource, resource: { ...resource.resource, text: 1 } },
      { ...resource, resource: { ...resource.resource, text: 'x'.repeat(65537) } }];
    assert.equal(api.appendMcpResources(container, undefined), 0);
    assert.equal(api.appendMcpResources(container, {}), 0);
    assert.equal(api.appendMcpResources(container, bad), 0);
    assert.equal(api.appendMcpResources(container, [resource, resource]), 1);
    const frame = container.firstChild;
    assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
    assert.equal(frame.referrerPolicy, 'no-referrer');
    assert.equal(frame.srcdoc, resource.resource.text);
    assert.equal(container.childNodes.length, 1);
    const valid = { origin: 'null', source: frame.contentWindow, data: { type: 'laliga-panel:resize', height: 450 } };
    for (const event of [{ ...valid, origin: 'http://localhost:8000' }, { ...valid, data: null },
      { ...valid, data: { type: 'bad' } }, ...[0, -1, 1.5, 8193, '400'].map(height => ({ ...valid, data: { ...valid.data, height } })),
      { ...valid, source: null }]) api.resizeMcpResource(event);
    assert.equal(frame.style.height, '');
    api.resizeMcpResource(valid);
    assert.equal(frame.style.height, '450px');
    api.resizeMcpResource({ ...valid, data: { ...valid.data, height: 1 } });
    assert.equal(frame.style.height, '450px');
    h.window.dispatchEvent(new h.window.MessageEvent('message', { ...valid, data: { ...valid.data, height: 8192 } }));
    assert.equal(frame.style.height, '8192px');
  } finally { h.close(); }
});

test('resource module imports without a browser global and keeps minimum height', async () => {
  const h = createHarness({ browserless: true });
  try {
    const api = await h.load('mcp-ui.js');
    const frame = h.document.createElement('iframe');
    api.resizeMcpResource({ origin: 'null', source: frame.contentWindow, data: { type: 'laliga-panel:resize', height: 1 } }, [frame]);
    assert.equal(frame.style.height, '220px');
  } finally { h.close(); }
});

async function registeredHarness(options = {}) {
  const requests = [];
  const tools = [];
  let next = { ok: true, json: async () => ({ intent: 'table', answer: 'Verified answer', today_label: 'Today', facts: ['Fact'] }) };
  const h = createHarness({ fetch: async (url, request) => {
    requests.push({ url, body: JSON.parse(request.body) });
    if (next instanceof Error) throw next;
    return next;
  } });
  h.document.modelContext = { registerTool: async tool => { tools.push(tool); } };
  const api = await h.load('webmcp.js');
  await api.setupWebMcp(options);
  return { h, api, tools, requests, respond(value) { next = value; } };
}

test('registered WebMCP tools build verified questions, require inputs, and are idempotent', async () => {
  const r = await registeredHarness({ today_label: 'Reference' });
  const { h, api, tools, requests } = r;
  try {
    assert.equal(tools.length, 5);
    assert.equal(api.webmcpStatus().available, true);
    const status = api.webmcpStatus();
    status.tools.length = 0;
    assert.equal(api.webmcpStatus().tools.length, 5);
    await api.setupWebMcp();
    assert.equal(tools.length, 5);
    const cases = [
      [0, { team: ' madrid ' }, 'cuando juega el proximo partido del madrid'],
      [0, { team: 'madrid', opponent: 'barca' }, 'cuando juega el proximo partido entre madrid y barca'],
      [1, { team: 'barca' }, 'como salio el ultimo partido del barca'],
      [2, undefined, 'tabla de posiciones'], [3, 'invalid input', 'quienes estan en descenso'],
      [4, { question: ' consulta ' }, 'consulta'],
    ];
    for (const [index, args, expected] of cases) {
      const result = await tools[index].execute(args);
      assert.equal(requests.at(-1).body.question, expected);
      assert.match(result.content[0].text, /Verified answer/);
      assert.match(result.content[0].text, /Hechos verificados/);
      assert.match(result.content[0].text, /Referencia: Today/);
    }
    const before = requests.length;
    for (const index of [0, 1, 4]) {
      for (const args of [undefined, null, {}, { team: 4, question: [] }]) {
        assert.match((await tools[index].execute(args)).content[0].text, /Necesito/);
      }
    }
    assert.equal(requests.length, before);
    // Agent arguments can be proxies; a failed property read must fail closed.
    const proxy = new Proxy({}, { get() { throw new Error('unreadable argument'); } });
    assert.match((await tools[0].execute(proxy)).content[0].text, /Necesito/);
    r.respond({ ok: true, json: async () => ({ intent: 'none', answer: 'Do not publish', facts: [] }) });
    assert.match((await tools[2].execute()).content[0].text, /No tengo ese dato verificado/);
    r.respond({ ok: true, json: async () => ({ intent: 'table', answer: 'No facts', facts: 'bad' }) });
    assert.doesNotMatch((await tools[2].execute()).content[0].text, /Hechos verificados/);
    r.respond({ ok: true, json: async () => null });
    assert.match((await tools[2].execute()).content[0].text, /No tengo ese dato verificado/);
    r.respond({ ok: false, status: 503 });
    assert.match((await tools[2].execute()).content[0].text, /No pude consultar/);
    r.respond(new Error('offline'));
    assert.match((await tools[2].execute()).content[0].text, /localhost:8000/);
  } finally { h.close(); }
});

test('WebMCP reference fallback and registration failures remain nonfatal', async () => {
  for (const mode of ['missing', 'invalid', 'partial', 'zero', 'getter', 'delayed', 'deadline']) {
    const h = createHarness({ fetch: async () => ({ ok: true, json: async () => ({ answer: 'Verified', facts: [] }) }) });
    const tools = [];
    try {
      // An injected clock avoids spending 2.5 seconds on unavailable-polyfill tests.
      let clock = 0;
      h.window.Date.now = () => { clock += 900; return clock; };
      h.window.setTimeout = callback => { callback(); return 0; };
      const context = { registerTool: async tool => {
        if (mode === 'zero' || mode === 'partial' && tools.length === 1) throw new Error('registration denied');
        tools.push(tool);
      } };
      if (mode === 'getter') Object.defineProperty(h.document, 'modelContext', { get() { throw new Error('access denied'); } });
      else if (mode === 'delayed' || mode === 'deadline') {
        let reads = 0;
        Object.defineProperty(h.document, 'modelContext', { get() { reads++; return reads >= (mode === 'delayed' ? 2 : 3) ? context : null; } });
      } else if (mode !== 'missing') h.document.modelContext = mode === 'invalid' ? {} : context;
      const api = await h.load('webmcp.js');
      const status = await api.setupWebMcp();
      assert.equal(status.available, mode === 'delayed' || mode === 'deadline');
      if (status.available) {
        const text = (await tools[2].execute()).content[0].text;
        assert.match(text, /Referencia: fecha no disponible/);
      }
      if (mode === 'partial') assert.equal(status.tools.length, 1);
      if (['missing', 'invalid', 'zero', 'getter'].includes(mode)) assert.equal(status.tools.length, 0);
    } finally { h.close(); }
  }
});
