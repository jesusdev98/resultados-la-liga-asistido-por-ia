const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./harness.cjs');
const fixture = (extra = {}) => ({ local: 'Real Madrid', visitante: 'Alavés', dia: 'domingo 29 de noviembre de 2026', hora: 'Por definir', horario_confirmado: false, ...extra });
const prose = match => `${match.local} juega contra ${match.visitante} el ${match.dia}${match.horario_confirmado ? ` a las ${match.hora}` : ''}.`;
const state = match => match.horario_confirmado ? 'Fecha y horario confirmados' : 'Fecha provisional y horario por definir';

async function run(matches, outputs) {
  const requests = [], calls = [], diagnostics = [];
  const h = createHarness({ fetch: async (url, request) => {
    calls.push(JSON.parse(request.body));
    return { ok: true, json: async () => ({ ok: true, datos: { partidos: matches }, content: [] }) };
  } });
  h.window.__LALIGA_DIAGNOSTICS__ = event => diagnostics.push(event);
  const engine = { chat: { completions: { create: async request => {
    requests.push(request);
    const schema = request.response_format && JSON.parse(request.response_format.schema);
    const text = schema?.properties?.herramienta ? '{"herramienta":"proximo_partido"}' : outputs.shift();
    return { choices: [{ message: { content: text } }] };
  } } } };
  try {
    const api = await h.load('agent.js');
    const result = await api.runAgent({ engine, question: 'próximos partidos del madrid', continuation: { equipo: 'Real Madrid', cantidad: matches.length } });
    return { result, requests, calls, diagnostics };
  } finally { h.close(); }
}

test('fixture writer requests structured input and separate model-generated prose/status', async () => {
  for (const match of [fixture(), fixture({ hora: '21:00', horario_confirmado: true })]) {
    const generated = { respuesta: prose(match), estado: state(match) };
    const result = await run([match], [JSON.stringify(generated)]);
    assert.equal(result.result.answer, `${generated.respuesta}\n${generated.estado}`);
    assert.equal(result.calls.length, 1);
    const writer = result.requests[1];
    const schema = JSON.parse(writer.response_format.schema);
    assert.deepEqual(schema.required, ['respuesta', 'estado']);
    assert.equal(schema.additionalProperties, false);
    assert.deepEqual(schema.properties.estado.enum, [state(match)]);
    assert.equal(schema.properties.respuesta.type, 'string');
    const input = JSON.parse(writer.messages[0].content.split('\nDATOS: ')[1]);
    assert.equal(input.partidos[0].estado, state(match));
    assert.equal(input.partidos[0].local, match.local);
    assert.doesNotMatch(result.result.answer, /[{}]|"respuesta"|"estado"/);
    assert.equal(result.diagnostics.find(event => event.stage === 'writer').format, 'fixture-json/v1');
  }
});

test('an implicit singleton may retain context without repeating both club names', async () => {
  const match = fixture({ hora: '16:15', horario_confirmado: true, dia: 'domingo 1 de noviembre de 2026' });
  const generated = { respuesta: 'El próximo partido se jugará el domingo 1 de noviembre de 2026 a las 16:15 horas.', estado: state(match) };
  const out = await run([match], [JSON.stringify(generated)]);
  assert.equal(out.result.answer, `${generated.respuesta}\n${generated.estado}`);
  assert.equal(out.calls.length, 1);
  // A list cannot use that same ambiguous sentence for either individual fixture.
  const list = await run([match, fixture()], [JSON.stringify(generated)]);
  assert.equal(list.result.answer, '');
  assert.equal(list.calls.length, 1);
});

test('structured fixture failures never become deterministic or unchecked answers', async () => {
  const match = fixture();
  const valid = { respuesta: prose(match), estado: state(match) };
  for (const output of ['not JSON', 'null', '[]', '{}', JSON.stringify({ ...valid, extra: 'ignored?' }),
    JSON.stringify({ ...valid, respuesta: null }), JSON.stringify({ ...valid, respuesta: '' }),
    JSON.stringify({ respuesta: valid.respuesta }), JSON.stringify({ ...valid, estado: 'Fecha confirmada' }),
    JSON.stringify({ ...valid, respuesta: 'La fecha es provisional.' }),
    JSON.stringify({ ...valid, respuesta: valid.respuesta + ' a las 22:00' }),
    JSON.stringify({ ...valid, respuesta: '{"local":"Real Madrid"}' }),
    JSON.stringify({ ...valid, respuesta: valid.respuesta + ' {"extra":1}' }),
    JSON.stringify({ ...valid, respuesta: valid.respuesta + ' La fecha está confirmada.' })]) {
    const out = await run([match], [output]);
    assert.equal(out.result.answer, '', output);
    assert.equal(out.calls.length, 1);
  }
  const confirmed = fixture({ horario_confirmado: true, hora: '21:00' });
  const wrongStatus = await run([confirmed], [JSON.stringify({ respuesta: prose(confirmed), estado: state(match) })]);
  assert.equal(wrongStatus.result.answer, '');
  assert.ok(wrongStatus.diagnostics.some(event => event.reason === 'confirmed_status'));
});

test('structured status corrections retain one global budget and atomic publication', async () => {
  const first = fixture();
  const second = fixture({ visitante: 'Valencia', dia: 'domingo 6 de diciembre de 2026' });
  const good = match => ({ respuesta: prose(match), estado: state(match) });
  const bad = match => ({ ...good(match), estado: 'Horario por definir' });
  const out = await run([first, second], [bad(first), good(first), bad(second)].map(JSON.stringify));
  assert.equal(out.result.answer, '');
  assert.equal(out.calls.length, 1);
  assert.equal(out.requests.length, 4); // selector + first + rewrite + second, never another query/rewrite.
  assert.equal(out.diagnostics.filter(event => event.stage === 'writer_correction').length, 1);
});
