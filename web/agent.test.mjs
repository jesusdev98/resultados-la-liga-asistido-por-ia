import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = await readFile(new URL('./agent.js', import.meta.url), 'utf8');
const agent = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const { runAgent, replayCalls } = agent;
const content = [{ type: 'resource', resource: { uri: 'ui://prueba-deepseek/panel', mimeType: 'text/html', text: '<p>Fecha provisional</p>' } }];
const datos = { partidos: [{ local: 'Real Madrid', visitante: 'Atlético Madrid', dia: 'domingo 4 de abril de 2027', hora: 'Por definir', estado_fecha: 'Fecha provisional', horario_confirmado: false, fecha_iso: '2027-04-04T18:00Z' }] };
const isSelector = request => Boolean(request.response_format && JSON.parse(request.response_format.schema).properties.herramienta);
const fixtureText = (request, respuesta, estado) => JSON.stringify({ respuesta,
  estado: estado || JSON.parse(request.response_format.schema).properties.estado.enum[0] });
const status = match => match.horario_confirmado ? 'Fecha y horario confirmados' : 'Fecha provisional y horario por definir';

test('opt-in diagnostics capture rejection evidence without changing model output', async () => {
  const originalFetch = globalThis.fetch;
  const originalObserver = globalThis.__LALIGA_DIAGNOSTICS__;
  const observed = [];
  const engine = { chat: { completions: { create: async request => ({ choices: [{
    finish_reason: 'stop', message: { content: isSelector(request)
      ? '{"herramienta":"proximo_partido"}' : fixtureText(request, 'Real Madrid vs Atlético Madrid el domingo 4 de abril de 2027 a las 20:00.') },
  }] }) } } };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, datos, content }) });
  try {
    globalThis.__LALIGA_DIAGNOSTICS__ = event => observed.push(event);
    assert.equal((await runAgent({ engine, question: 'proximo madrid atleti' })).answer, '');
    assert.equal(observed[0].stage, 'selector');
    assert.ok(observed.some(event => event.stage === 'writer' && event.text.includes('20:00')));
    assert.ok(observed.some(event => event.reason === 'unsupported_date_or_time'));
    globalThis.__LALIGA_DIAGNOSTICS__ = () => { throw new Error('observer failure'); };
    assert.equal((await runAgent({ engine, question: 'proximo madrid atleti' })).answer, '');
  } finally {
    globalThis.fetch = originalFetch;
    if (originalObserver === undefined) delete globalThis.__LALIGA_DIAGNOSTICS__;
    else globalThis.__LALIGA_DIAGNOSTICS__ = originalObserver;
  }
});

test('unsupported questions cannot publish invented facts even if the model ignores its schema', async () => {
  for (const answer of [
    'Messi ganó el Balón de Oro con el Real Madrid y tiene 27 puntos.',
    'No tengo datos verificados sobre esa consulta. Puedo ayudarte con resultados, próximos partidos y clasificación de La Liga.',
  ]) {
    const engine = { chat: { completions: { create: async request => {
      assert.ok(request.response_format);
      assert.doesNotMatch(request.messages[0].content, /27 puntos|context_text/);
      return { choices: [{ message: { content: JSON.stringify({ answer }) } }] };
    } } } };
    const reply = await agent.runScopeReply({ engine, question: 'dime como quedo el balon de oro' });
    assert.equal(reply, answer.startsWith('No tengo') ? answer : '');
  }
});

test('the resource renderer only admits our resource and creates an unprivileged frame', async () => {
  const original = globalThis.document;
  const uiSource = await readFile(new URL('./mcp-ui.js', import.meta.url), 'utf8');
  const { appendMcpResources } = await import(`data:text/javascript;base64,${Buffer.from(uiSource).toString('base64')}`);
  const frames = [];
  globalThis.document = { createElement: (tag) => ({ tag, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; } }) };
  const container = { appendChild: (frame) => frames.push(frame) };
  try {
    assert.equal(appendMcpResources(container, content), 1);
    assert.equal(frames[0].tag, 'iframe');
    assert.equal(frames[0].attributes.sandbox, 'allow-scripts');
    assert.equal(frames[0].srcdoc, content[0].resource.text);
    assert.equal(appendMcpResources(container, [{ type: 'resource', resource: { uri: 'https://evil.example', mimeType: 'text/html', text: '<p>x</p>' } }]), 0);
    assert.equal(appendMcpResources(container, null), 0);
  } finally { globalThis.document = original; }
});

test('only the corresponding opaque-origin iframe may request bounded height changes', async () => {
  const uiSource = await readFile(new URL('./mcp-ui.js', import.meta.url), 'utf8');
  const { resizeMcpResource } = await import(`data:text/javascript;base64,${Buffer.from(uiSource).toString('base64')}`);
  const frame = { contentWindow: {}, style: {} };
  const event = { source: frame.contentWindow, origin: 'null', data: { type: 'laliga-panel:resize', height: 215 } };
  resizeMcpResource(event, [frame]);
  assert.equal(frame.style.height, '220px');
  resizeMcpResource({ ...event, data: { ...event.data, height: 420 } }, [frame]);
  assert.equal(frame.style.height, '420px');
  for (const invalid of [
    { ...event, source: {} }, { ...event, origin: 'https://evil.example' },
    { ...event, data: { type: 'other', height: 500 } },
    { ...event, data: { type: 'laliga-panel:resize', height: 1000000 } },
    { ...event, data: { type: 'laliga-panel:resize', height: '500' } },
  ]) resizeMcpResource(invalid, [frame]);
  assert.equal(frame.style.height, '420px');
});

test('local engine receives facts only; resource survives initial execution and replay', async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, datos, content }) });
  const engine = { chat: { completions: { create: async (request) => {
    requests.push(request);
    return { choices: [{ message: { content: isSelector(request) ? '{"herramienta":"proximo_partido","argumentos":{}}' : fixtureText(request, 'Real Madrid vs Atlético Madrid el domingo 4 de abril de 2027.') } }] };
  } } } };
  try {
    const turn = await runAgent({ engine, question: 'cuando juega madrid atleti', teams: ['Real Madrid', 'Atlético Madrid'] });
    assert.deepEqual(turn.calls[0].content, content);
    assert.match(requests[1].messages[0].content, /Fecha provisional/);
    assert.doesNotMatch(requests[1].messages[0].content, /ui:\/\/|<p>|18:00/);
    assert.deepEqual((await replayCalls(turn.calls))[0].content, content);
    assert.match(turn.answer, /por definir/);
  } finally { globalThis.fetch = original; }
});

test('a fabricated kickoff or omitted provisional state fails closed', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, datos, content }) });
  try {
    for (const prose of ['Juegan el 4 de abril de 2027 a las 20:00.', 'Juegan el 4 de abril de 2027.',
      'Juegan el 4 de abril de 2027. Fecha provisional y horario por definir. Fecha y horario confirmados.']) {
      const engine = { chat: { completions: { create: async (request) => ({ choices: [{ message: { content: isSelector(request) ? '{"herramienta":"proximo_partido"}' : fixtureText(request, prose, 'Horario por definir') } }] }) } } };
      const turn = await runAgent({ engine, question: 'proximo madrid atleti' });
      assert.equal(turn.answer, '');
      assert.deepEqual(turn.calls[0].content, content);
    }
  } finally { globalThis.fetch = original; }
});

test('a contextual next request reuses the verified query and survives replay', async () => {
  const original = globalThis.fetch;
  const sent = [];
  let modelCalls = 0;
  globalThis.fetch = async (url, request) => {
    sent.push(JSON.parse(request.body));
    return { ok: true, json: async () => ({ ok: true, datos, content }) };
  };
  const args = { equipo: 'Real Madrid', rival: 'Atlético Madrid', despues_de: '2026-10-25T20:00Z' };
  const engine = { chat: { completions: { create: async (request) => {
    modelCalls++;
    return { choices: [{ message: { content: isSelector(request)
      ? '{"herramienta":"proximo_partido","argumentos":{}}'
      : fixtureText(request, 'Real Madrid vs Atlético Madrid el domingo 4 de abril de 2027.') } }] };
  } } } };
  try {
    const turn = await runAgent({ engine, question: 'vale y el siguiente a ese?', continuation: args });
    assert.deepEqual(sent[0], { name: 'proximo_partido', args });
    await replayCalls(turn.calls);
    assert.deepEqual(sent[1], sent[0]);
    assert.equal(modelCalls, 2);
  } finally { globalThis.fetch = original; }
});

test('grounded queries ask the model for the tool only, without regenerating verified arguments', async () => {
  const original = globalThis.fetch;
  const requests = [];
  const sent = [];
  const engine = { chat: { completions: { create: async request => {
    requests.push(request);
    return { choices: [{ message: { content: isSelector(request)
      ? '{"herramienta":"proximo_partido"}'
      : fixtureText(request, 'Real Madrid vs Atlético Madrid el domingo 4 de abril de 2027.') } }] };
  } } } };
  globalThis.fetch = async (_url, req) => {
    sent.push(JSON.parse(req.body));
    return { ok: true, json: async () => ({ ok: true, datos, content }) };
  };
  const args = { equipo: 'Real Madrid', cantidad: 1, despues_de: '2026-10-25T20:00Z' };
  try {
    await runAgent({ engine, question: 'y el siguiente?', continuation: args });
    const schema = JSON.parse(requests[0].response_format.schema);
    assert.deepEqual(Object.keys(schema.properties), ['herramienta']);
    assert.ok(schema.properties.herramienta.enum.includes('tabla'), 'The model still selects the tool.');
    assert.ok(requests[0].max_tokens <= 32);
    assert.deepEqual(sent[0], { name: 'proximo_partido', args });
    requests.length = 0;
    await runAgent({ engine, question: 'cuando juega el madrid', teams: ['Real Madrid'] });
    assert.ok(JSON.parse(requests[0].response_format.schema).properties.argumentos);
  } finally { globalThis.fetch = original; }
});

test('a compact-selector mismatch never executes an incompatible tool or bypasses model choice', async () => {
  const original = globalThis.fetch;
  let selections = 0;
  let calls = 0;
  const engine = { chat: { completions: { create: async request => {
    assert.ok(request.response_format);
    selections++;
    return { choices: [{ message: { content: '{"herramienta":"tabla"}' } }] };
  } } } };
  globalThis.fetch = async () => { calls++; throw new Error('Must not call an incompatible tool'); };
  try {
    const turn = await runAgent({ engine, question: 'los 3 proximos del madrid', continuation: { equipo: 'Real Madrid', cantidad: 3 } });
    assert.equal(selections, 2);
    assert.equal(calls, 0);
    assert.equal(turn.answer, '');
    assert.deepEqual(turn.calls, []);
  } finally { globalThis.fetch = original; }
});

test('multi-fixture answers use one short grounded model narration per match', async () => {
  const original = globalThis.fetch;
  const partidos = [1, 8, 15].map((day, i) => ({
    local: 'Real Madrid', visitante: ['Racing Santander', 'Valencia', 'Sevilla'][i],
    dia: `domingo ${day} de noviembre de 2026`, hora: '16:15', horario_confirmado: true,
  }));
  const lines = partidos.map(m => `${m.local} vs ${m.visitante}, ${m.dia} a las ${m.hora}, fecha confirmada.`);
  globalThis.fetch = async (_url, req) => {
    assert.equal(JSON.parse(req.body).args.cantidad, 3);
    return { ok: true, json: async () => ({ ok: true, datos: { partidos }, content }) };
  };
  try {
    for (const mode of ['valid', 'omitted', 'contradictory']) {
      let writes = 0;
      const engine = { chat: { completions: { create: async request => {
        if (isSelector(request)) return { choices: [{ message: { content: '{"herramienta":"proximo_partido"}' } }] };
        const supplied = JSON.parse(request.messages[0].content.split('\nDATOS: ')[1]);
        assert.equal(supplied.partidos.length, 1);
        assert.equal(request.max_tokens, 160);
        assert.equal(supplied.partidos[0].visitante, partidos[writes].visitante);
        assert.doesNotMatch(request.messages[1].content, /tres|3/);
        let prose = lines[writes++];
        if (mode === 'omitted' && writes === 2) prose = lines[0];
        if (mode === 'contradictory' && writes === 2) prose = prose.replace('fecha confirmada.', 'fecha provisional y horario por definir.');
        return { choices: [{ message: { content: fixtureText(request, prose) } }] };
      } } } };
      const turn = await runAgent({ engine, question: 'tres proximos partidos del madrid', continuation: { equipo: 'Real Madrid', cantidad: 3 } });
      assert.equal(turn.answer, mode === 'valid' ? lines.map((line, index) => `${line}\n${status(partidos[index])}`).join('\n') : '');
      assert.equal(writes, mode === 'valid' ? 3 : 2);
    }
  } finally { globalThis.fetch = original; }
});

test('mixed fixture statuses can be narrated in separate sentences without cross-match confusion', async () => {
  const original = globalThis.fetch;
  const matches = [
    { local: 'Levante', visitante: 'Atlético Madrid', dia: 'sábado 31 de octubre de 2026', hora: '14:00', horario_confirmado: true },
    { local: 'Atlético Madrid', visitante: 'Sevilla', dia: 'domingo 8 de noviembre de 2026', hora: 'Por definir', horario_confirmado: false },
    { local: 'Getafe', visitante: 'Atlético Madrid', dia: 'domingo 22 de noviembre de 2026', hora: '18:30', horario_confirmado: true },
  ];
  let writes = 0;
  const responses = matches.map(m => `${m.local} juega contra ${m.visitante} el ${m.dia}. ` +
    (m.horario_confirmado ? `A las ${m.hora}, fecha confirmada.` : 'Fecha provisional y horario por definir.'));
  const engine = { chat: { completions: { create: async request => ({ choices: [{ message: { content:
    isSelector(request) ? '{"herramienta":"proximo_partido"}' : fixtureText(request, responses[writes++]) } }] }) } } };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, datos: { partidos: matches }, content }) });
  try {
    const turn = await runAgent({ engine, question: 'dime los 3 proximos partidos del atleti', continuation: { equipo: 'Atlético Madrid', cantidad: 3 } });
    assert.equal(writes, 3);
    assert.equal(turn.answer, responses.map((line, index) => `${line}\n${status(matches[index])}`).join('\n'));
    assert.equal(turn.calls.length, 1);
  } finally { globalThis.fetch = original; }
});

test('fixture writer prompts stay compact while preserving verbatim evidence and status', async () => {
  const original = globalThis.fetch;
  let system;
  const engine = { chat: { completions: { create: async request => {
    if (isSelector(request)) return { choices: [{ message: { content: '{"herramienta":"proximo_partido"}' } }] };
    system = request.messages[0].content;
    return { choices: [{ message: { content: fixtureText(request, 'Real Madrid vs Atlético Madrid el domingo 4 de abril de 2027.') } }] };
  } } } };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ok: true, datos, content }) });
  try {
    const turn = await runAgent({ engine, question: 'cuando juega madrid atleti' });
    assert.ok(turn.answer);
    assert.ok(system.length < 900, `Fixture prompt unexpectedly expanded to ${system.length} characters`);
    const evidence = JSON.parse(system.split('\nDATOS: ')[1]);
    assert.deepEqual(Object.keys(evidence.partidos[0]), ['local', 'visitante', 'dia', 'hora', 'estado']);
    assert.match(system, /Fecha provisional/);
    assert.match(system, /por definir/);
    assert.doesNotMatch(system, /fecha_iso|estadio"|2026-10-25T/);
  } finally { globalThis.fetch = original; }
});

test('an omitted provisional date gets one targeted rewrite, not a new query or whole-list retry', async () => {
  const original = globalThis.fetch;
  const matches = [
    { local: 'Valencia', visitante: 'Real Madrid', dia: 'sábado 7 de noviembre de 2026', hora: '21:00', horario_confirmado: true },
    { local: 'Real Madrid', visitante: 'Celta Vigo', dia: 'sábado 21 de noviembre de 2026', hora: '21:00', horario_confirmado: true },
    { local: 'Real Madrid', visitante: 'Alavés', dia: 'domingo 29 de noviembre de 2026', hora: 'Por definir', horario_confirmado: false },
  ];
  let queries = 0;
  const writes = [];
  const engine = { chat: { completions: { create: async request => {
    if (isSelector(request)) return { choices: [{ message: { content: '{"herramienta":"proximo_partido"}' } }] };
    writes.push(request);
    const m = matches[Math.min(writes.length - 1, 2)];
    const text = writes.length <= 2 ? `${m.local} vs ${m.visitante}, ${m.dia}, ${m.hora}.`
      : writes.length === 3 ? `${m.local} vs ${m.visitante}, ${m.dia}. La hora exacta aún no está definida.`
        : `Fecha provisional y horario por definir. ${m.local} vs ${m.visitante}, ${m.dia}.`;
    return { choices: [{ message: { content: fixtureText(request, text, writes.length === 3 ? 'Horario por definir' : undefined) } }] };
  } } } };
  globalThis.fetch = async () => { queries++; return { ok: true, json: async () => ({ ok: true, datos: { partidos: matches }, content }) }; };
  try {
    const turn = await runAgent({ engine, question: 'y los 3 proximos despues de ese del madrid?', continuation: { equipo: 'Real Madrid', cantidad: 3 } });
    assert.ok(turn.answer.includes('Fecha provisional y horario por definir'));
    assert.equal(writes.length, 4);
    assert.equal(queries, 1);
    assert.match(writes[3].messages[0].content, /CORRECCIÓN/);
    const instruction = writes[3].messages[0].content.split('\n').find(line => line.startsWith('CORRECCIÓN:'));
    assert.ok(instruction.length <= 220, 'Correction must stay short for the local 1.5B model.');
    assert.doesNotMatch(instruction, /Empieza literalmente|Después indica/);
    assert.match(instruction, /solo la oración final/);
    assert.doesNotMatch(writes[3].messages[0].content, /Valencia|Celta Vigo/);
  } finally { globalThis.fetch = original; }
});

test('head-to-head result question sends both teams to the model-selected result tool', async () => {
  const original = globalThis.fetch;
  let sent;
  const engine = { chat: { completions: { create: async (request) => request.response_format
    ? { choices: [{ message: { content: '{"herramienta":"ultimo_resultado","argumentos":{}}' } }] }
    : { choices: [{ message: { content: 'El Barcelona perdió 1-2 ante el Real Madrid el domingo 25 de octubre de 2026.' } }] } } } };
  globalThis.fetch = async (_url, request) => {
    sent = JSON.parse(request.body);
    return { ok: true, json: async () => ({ ok: true, datos: {
      local: 'Barcelona', visitante: 'Real Madrid', goles_local: 1, goles_visitante: 2,
      dia: 'domingo 25 de octubre de 2026',
    } }) };
  };
  try {
    const turn = await runAgent({ engine, question: 'cuanto quedo el madrid barsa?', teams: ['Real Madrid', 'Barcelona'] });
    assert.deepEqual(sent, { name: 'ultimo_resultado', args: { equipo: 'Real Madrid', rival: 'Barcelona' } });
    assert.match(turn.answer, /1-2/);
  } finally { globalThis.fetch = original; }
});
