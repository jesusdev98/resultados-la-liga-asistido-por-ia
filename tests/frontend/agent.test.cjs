const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./harness.cjs');

const response = text => ({ choices: [{ message: { content: text }, finish_reason: 'stop' }], usage: { prompt_tokens: 2, completion_tokens: 1 } });
const choice = (name = 'proximo_partido', argumentos = {}) => JSON.stringify({ herramienta: name, argumentos });
const fixture = (overrides = {}) => ({ local: 'Real Madrid', visitante: 'Sevilla', dia: 'domingo 8 de noviembre de 2026', hora: 'Por definir', horario_confirmado: false, ...overrides });
const prose = match => `${match.local} juega contra ${match.visitante} el ${match.dia}. ${match.horario_confirmado ? `A las ${match.hora}.` : 'Fecha provisional y horario por definir.'}`;
const plain = value => JSON.parse(JSON.stringify(value));
const fixtureState = match => match.horario_confirmado ? 'Fecha y horario confirmados' : 'Fecha provisional y horario por definir';
const published = match => `${prose(match)}\n${fixtureState(match)}`;

test('unknown model tool and nameless engine rejection fail closed without a performance API', async () => {
  const events = [];
  const h = createHarness({ fetch: async (url, request) => {
    const { name } = JSON.parse(request.body);
    return { ok: true, json: async () => name === 'unknown' ? { ok: false, error: 'Unknown tool' } : { ok: true, datos: {} } };
  } });
  Object.defineProperty(h.window, 'performance', { value: undefined, configurable: true });
  h.window.__LALIGA_DIAGNOSTICS__ = event => events.push(event);
  let invocation = 0;
  const engine = { chat: { completions: { create: async () => {
    invocation++;
    if (invocation === 1) return response(choice('unknown'));
    if (invocation === 2) return response(choice('tabla'));
    throw Object.create(null);
  } } } };
  try {
    const api = await h.load('agent.js');
    const result = await api.runAgent({ engine, question: 'tabla' });
    assert.equal(result.answer, '');
    assert.equal(result.calls.length, 2);
    assert.equal(result.calls[0].ok, false);
    assert.equal(result.calls[1].ok, true);
    assert.equal(events.find(event => event.stage === 'writer_error').name, 'Error');
    assert.ok(events.filter(event => event.elapsed_ms !== undefined).every(event => Number.isFinite(event.elapsed_ms)));
  } finally { h.close(); }
});

async function scenario({ selections = [choice()], writes = ['Respuesta verificada.'], datos = {}, fetcher, options = {}, observer } = {}) {
  const requests = [], sent = [], events = [], statuses = [];
  const h = createHarness({ fetch: async (url, request) => {
    sent.push(JSON.parse(request.body));
    return fetcher ? fetcher(url, request) : { ok: true, json: async () => ({ ok: true, datos, content: ['card'] }) };
  } });
  if (observer !== false) h.window.__LALIGA_DIAGNOSTICS__ = observer || (event => events.push(event));
  const engine = { chat: { completions: { create: async request => {
    requests.push(request);
    const schema = request.response_format && JSON.parse(request.response_format.schema);
    const value = (schema?.properties?.herramienta ? selections : writes).shift();
    if (value instanceof Error) throw value;
    if (typeof value === 'string' && schema?.properties?.respuesta) {
      const expected = schema.properties.estado.enum[0];
      const estado = expected.includes('provisional') && !/provisional.*por definir/i.test(value) ? 'Horario por definir' : expected;
      return response(JSON.stringify({ respuesta: value, estado }));
    }
    return typeof value === 'string' ? response(value) : value;
  } } } };
  try {
    const api = await h.load('agent.js');
    const result = await api.runAgent({ engine, question: 'cuando juega madrid', onStatus: status => statuses.push(status), ...options });
    return { result: plain(result), requests: plain(requests), sent, events, statuses };
  } finally { h.close(); }
}

test('selector recovers JSON fences and prose wrappers without trusting undeclared arguments', async () => {
  for (const text of ['```json\n' + choice('tabla', { equipo: 'liga' }) + '\n```', 'Here is the selection: ' + choice('tabla') + ' end']) {
    const out = await scenario({ selections: [text] });
    assert.deepEqual(out.sent, [{ name: 'tabla', args: {} }]);
    assert.equal(out.result.answer, 'Respuesta verificada.');
  }
});

test('malformed selector values exhaust exactly two attempts without HTTP or prose', async () => {
  for (const text of ['', 'not JSON', 'null', '[]', '1', '{}', '{"herramienta":42}', '{"herramienta":" "}', '```json\ninvalid\n```']) {
    const out = await scenario({ selections: [text, text] });
    assert.deepEqual(out.result, { answer: '', calls: [] });
    assert.equal(out.requests.length, 2);
    assert.equal(out.sent.length, 0);
    assert.match(out.requests[1].messages[0].content, /corrección/);
  }
  for (const selections of [[new Error('engine unavailable'), choice()], [undefined, choice()]]) {
    const out = await scenario({ selections });
    assert.equal(out.result.answer, 'Respuesta verificada.');
    assert.deepEqual(out.statuses, ['consultando la API...', 'corrigiendo la consulta...', 'redactando...']);
  }
});

test('sanitization and resolver grounding preserve declared team/date/count contracts', async () => {
  const args = { equipo: ' Wrong ', rival: ' Wrong rival ', cantidad: 4, despues_de: ' 2026-10-25T20:00Z ', despues_del_dia: ' 2026-10-25 ', fecha_exacta: ' 2026-11-08 ', extra: 'discard' };
  const out = await scenario({ selections: [choice('proximo_partido', args)], options: { teams: [null, '', 'Real Madrid', 'Sevilla'] } });
  assert.deepEqual(out.sent[0].args, { equipo: 'Real Madrid', rival: 'Sevilla', cantidad: 4, despues_de: '2026-10-25T20:00Z', despues_del_dia: '2026-10-25', fecha_exacta: '2026-11-08' });
  const one = await scenario({ selections: [choice('ultimo_resultado', args)], options: { teams: ['Real Madrid'] } });
  assert.deepEqual(one.sent[0].args, { equipo: 'Real Madrid' });
  const position = await scenario({ selections: [choice('posicion', args)], options: { teams: ['Real Madrid'] } });
  assert.deepEqual(position.sent[0].args, { equipo: 'Real Madrid' });
  for (const cantidad of [0, 5, 1.5, '3', 1]) {
    const out = await scenario({ selections: [choice('proximo_partido', { equipo: ' ', rival: 12, cantidad })], options: { teams: [] } });
    assert.deepEqual(out.sent[0].args, cantidad === 1 ? { cantidad: 1 } : {});
  }
  for (const argumentos of [[], null, 'invalid']) {
    assert.deepEqual((await scenario({ selections: [choice('proximo_partido', argumentos)] })).sent[0].args, {});
  }
});

test('continuation retains verified dimensions only after model selects compatible tool', async () => {
  const continuation = { equipo: 'Real Madrid', cantidad: 3, despues_de: '2026-10-25T20:00Z' };
  const out = await scenario({ selections: [choice('tabla'), choice()], options: { continuation, context: { previousQuestion: 'previous' } } });
  assert.deepEqual(out.sent, [{ name: 'proximo_partido', args: continuation }]);
  assert.equal(out.requests[0].max_tokens, 32);
  assert.deepEqual(Object.keys(JSON.parse(out.requests[0].response_format.schema).properties), ['herramienta']);
  assert.equal((await scenario({ selections: [choice('tabla'), choice('tabla')], options: { continuation } })).sent.length, 0);
  const failed = await scenario({ options: { continuation }, fetcher: async () => ({ ok: false, status: 503 }) });
  assert.equal(failed.sent.length, 1);
  assert.equal(failed.requests.length, 1);
});

test('HTTP, connection, invalid JSON and tool errors remain bounded and fail closed', async () => {
  for (const [fetcher, expected] of [
    [async () => ({ ok: false, status: 500 }), /HTTP 500/],
    [async () => { throw new Error('secret network'); }, /conectar/],
    [async () => ({ ok: true, json: async () => { throw new SyntaxError('secret JSON'); } }), /conectar/],
    [async () => ({ ok: true, json: async () => ({ ok: false, error: 'Tool failed' }) }), /Tool failed/],
    [async () => ({ ok: true, json: async () => null }), /no devolvió datos/],
    [async () => ({ ok: true, json: async () => ({}) }), /no devolvió datos/],
  ]) {
    const out = await scenario({ selections: [choice(), choice()], fetcher });
    assert.equal(out.result.answer, '');
    assert.equal(out.result.calls.length, 2);
    assert.match(out.result.calls[0].error, expected);
    assert.equal(out.requests.length, 2);
  }
});

test('all non-fixture writer payloads preserve evidence and use appropriate tasks', async () => {
  const cases = [
    ['ultimo_resultado', { local: 'Madrid', visitante: 'Sevilla', goles_local: 2, goles_visitante: 1 }, /pasado/],
    ['posicion', { equipo: 'Madrid', pos: 1, puntos: 30 }, /puesto/],
    ['descenso', { filas: [{ equipo: 'Sevilla', puntos: 3 }] }, /descenso/],
    ['tabla', { filas: [{ equipo: 'Madrid', jugados: 10, puntos: 30 }] }, /lidera/],
    ['tabla', { filas: Array.from({ length: 20 }, (_, i) => ({ equipo: `Club ${i}`, jugados: 10, puntos: 30 - i })) }, /lidera/],
    ['tabla', { filas: [] }, /responda la pregunta/],
    ['tabla', null, /responda la pregunta/],
  ];
  for (const [name, datos, task] of cases) {
    const out = await scenario({ selections: [choice(name)], datos });
    const prompt = out.requests[1].messages[0].content;
    assert.match(prompt, task);
    const projected = JSON.parse(prompt.split('\nDATOS: ')[1]);
    if (datos?.filas?.length === 20) {
      assert.equal(projected.filas.length, 5);
      assert.equal(projected.total_equipos, 20);
    } else assert.deepEqual(projected, datos);
  }
});

test('singleton/multiple fixture narration preserves strict status, team and date guards', async () => {
  const confirmed = fixture({ hora: '21:00', horario_confirmado: true });
  for (const match of [fixture(), confirmed]) {
    const out = await scenario({ datos: { partidos: [match] }, writes: [prose(match)] });
    assert.equal(out.result.answer, published(match));
    assert.equal(out.requests[1].max_tokens, 160);
  }
  const matches = [confirmed, fixture({ visitante: 'Valencia' })];
  const out = await scenario({ datos: { partidos: matches }, writes: matches.map(prose) });
  assert.equal(out.result.answer, matches.map(published).join('\n'));
  assert.deepEqual(out.requests.slice(1).map(request => request.max_tokens), [160, 160]);
  for (const text of ['', '{"partidos":[]}', '"local": "Madrid"', 'A las 19:00.', 'El 9 de noviembre de 2026.', 'El 2026-11-09.', prose(confirmed).replace('21:00', 'por confirmar'), 'Madrid juega.']) {
    const out = await scenario({ datos: { partidos: matches }, writes: [text] });
    assert.equal(out.result.answer, '');
    assert.equal(out.requests.length, 2);
  }
  for (const text of ['Fecha provisional.', 'Horario por definir.', 'Fecha provisional y horario por definir. Fecha confirmada.']) {
    const out = await scenario({ datos: { partidos: [fixture()] }, writes: [text] });
    assert.equal(out.result.answer, '');
  }
});

test('rewrite evidence retains echoed rejection and never publishes a partial list or re-queries', async () => {
  const matches = [fixture({ hora: '21:00', horario_confirmado: true }), fixture({ visitante: 'Alavés' })];
  const echo = 'Empieza literalmente con Fecha provisional y horario por definir. Después indica ambos equipos y el día de DATOS.';
  const out = await scenario({ datos: { partidos: matches }, writes: [prose(matches[0]), `${matches[1].local} vs ${matches[1].visitante}, ${matches[1].dia}. Horario por definir.`, echo] });
  assert.equal(out.result.answer, '');
  assert.equal(out.sent.length, 1);
  assert.equal(out.requests.length, 4);
  assert.equal(out.events.filter(event => event.stage === 'writer_correction').length, 1);
  assert.ok(out.events.some(event => event.stage === 'writer' && JSON.parse(event.text).respuesta === echo));
  assert.ok(out.events.some(event => event.reason === 'missing_fixture_fields' && event.text.includes(echo)));
});

test('one global rewrite budget is exhausted across separate provisional fixtures', async () => {
  const matches = [fixture(), fixture({ visitante: 'Alavés' }), fixture({ visitante: 'Valencia' })];
  const out = await scenario({ datos: { partidos: matches }, writes: ['Horario por definir.', prose(matches[0]), 'Horario por definir.', prose(matches[1])] });
  assert.equal(out.result.answer, '');
  assert.equal(out.sent.length, 1);
  assert.equal(out.requests.length, 4);
  assert.equal(out.events.filter(event => event.stage === 'writer_correction').length, 1);
});

test('writer failures and broken observers cannot escape fail-closed behavior', async () => {
  for (const writes of [[new Error('writer failed')], [undefined]]) {
    const isError = writes[0] instanceof Error;
    const out = await scenario({ writes });
    assert.equal(out.result.answer, '');
    if (isError) assert.ok(out.events.some(event => event.stage === 'writer_error'));
  }
  assert.equal((await scenario({ observer: () => { throw new Error('observer failed'); } })).result.answer, 'Respuesta verificada.');
  assert.equal((await scenario({ observer: false })).result.answer, 'Respuesta verificada.');
  assert.equal((await scenario({ options: { onStatus: null } })).result.answer, 'Respuesta verificada.');
});

test('public context, summaries, claim validation, replay and scope refusal cover malformed prior state', async () => {
  const sent = [];
  const h = createHarness({ fetch: async (_url, request) => {
    sent.push(JSON.parse(request.body));
    return sent.length === 1 ? { ok: true, json: async () => ({ ok: true, datos: {}, content: [] }) } : { ok: false, status: 502 };
  } });
  try {
    const api = await h.load('agent.js');
    for (const context of [undefined, { previousQuestion: 3 }, { previousQuestion: ' ' }, { previousQuestion: 'y madrid' }, { previousQuestion: 'prior', previousTool: 'tabla', previousArgs: { equipo: 'Madrid' } }]) {
      const prompt = api.buildSelectorPrompt(context, 'verified');
      assert.match(prompt, /verified/);
      if (typeof context?.previousQuestion === 'string' && context.previousQuestion.trim()) assert.match(prompt, /CONTEXTO/);
    }
    assert.equal(api.summarizeCalls(null), 'sin consultas');
    assert.equal(api.summarizeCalls([]), 'sin consultas');
    assert.equal(api.summarizeCalls([{ name: 'tabla', args: { equipo: 'ignore' }, ok: true }]), 'tabla()');
    assert.equal(api.summarizeCalls([{ name: 'custom', args: { a: '', b: null, c: undefined, d: 3 }, ok: false }, null]), 'custom(d=3) ✗ · ?() ✗');
    assert.equal(api.summarizeCalls([{ name: 'posicion', args: { equipo: 'Madrid', rival: 'ignore' }, ok: true }]), 'posicion(equipo=Madrid)');
    assert.deepEqual(plain(api.unsupportedClaims('19:00 19:00 2026-11-08 8 de noviembre de 2026', { fecha_iso: '2026-11-08T19:00Z', dia: '8 de noviembre de 2026' })), ['19:00']);
    assert.deepEqual(plain(api.unsupportedClaims(null, null)), []);
    assert.deepEqual(plain(api.unsupportedClaims('9 de noviembre de 2026 y 2026-11-09', {})), ['9 de noviembre de 2026', '2026-11-09']);
    assert.deepEqual(plain(await api.replayCalls(null)), []);
    const replay = await api.replayCalls([null, { ok: false }, { name: 'tabla', args: null, ok: true }, { name: 'unknown', args: [], ok: true }]);
    assert.equal(replay[0].ok, true);
    assert.equal(replay[1].ok, false);
    assert.deepEqual(sent, [{ name: 'tabla', args: {} }, { name: 'unknown', args: {} }]);
    for (const value of [undefined, 'bad JSON', '{"answer":"invented"}', JSON.stringify({ answer: 'No tengo datos verificados sobre esa consulta. Puedo ayudarte con resultados, próximos partidos y clasificación de La Liga.' }), JSON.stringify({ answer: 'Puedo ayudarte con resultados, próximos partidos y clasificación de La Liga, pero no tengo información verificada para responder esa pregunta.' }), new Error('engine failed')]) {
      const engine = { chat: { completions: { create: async () => { if (value instanceof Error) throw value; return value === undefined ? undefined : response(value); } } } };
      const result = await api.runScopeReply({ engine, question: 'awards' });
      assert.equal(result, typeof value === 'string' && /verificad[ao]/.test(value) ? JSON.parse(value).answer : '');
    }
  } finally { h.close(); }
});
