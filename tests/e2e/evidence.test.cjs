const test = require('node:test');
const assert = require('node:assert/strict');
const { verifyCase } = require('./verify-evidence.cjs');

function validEvidence() {
  const partidos = [8, 15, 22].map(day => ({ local: 'Real Madrid', visitante: 'Valencia',
    fecha_iso: `2026-11-${String(day).padStart(2, '0')}T17:00Z`, dia: `domingo ${day} de noviembre de 2026` }));
  return { id: 'following-three-numeric', expected: 3, observed: {
    network: [
      { url: '/api/ask', request: { fixture_context: { despues_de: '2026-11-01T17:00Z' } } },
      { url: '/api/tool', request: { args: { equipo: 'Real Madrid', cantidad: 3 } }, response: { ok: true, datos: { partidos } } },
    ], text: [partidos.map(m => `${m.local} vs ${m.visitante}, ${m.dia}`).join('\n')],
  } };
}

test('independent evidence validator accepts complete ordered following fixtures', () => {
  assert.doesNotThrow(() => verifyCase(validEvidence()));
});
test('evidence validator rejects wrong club and an unrequested opponent', () => {
  for (const change of [args => { args.equipo = 'Barcelona'; }, args => { args.rival = 'Barcelona'; }]) {
    const item = validEvidence();
    change(item.observed.network[1].request.args);
    assert.throws(() => verifyCase(item));
  }
});
test('evidence validator rejects repeated anchors and unordered fixtures', () => {
  const repeat = validEvidence();
  repeat.observed.network[1].response.datos.partidos[0].fecha_iso = '2026-11-01T17:00Z';
  assert.throws(() => verifyCase(repeat), /repeated or preceded/);
  const unordered = validEvidence();
  unordered.observed.network[1].response.datos.partidos.reverse();
  assert.throws(() => verifyCase(unordered), /chronological/);
});
test('supporting cards cannot hide an incomplete model answer', () => {
  const item = validEvidence();
  item.observed.text.unshift(item.observed.text[0]);
  item.observed.text[1] = 'El próximo partido es contra Valencia.';
  assert.throws(() => verifyCase(item), /Model prose/);
});
test('confirmations must keep the exact displayed timestamp', () => {
  const item = validEvidence();
  item.id = 'confirmation'; item.expected = 1;
  item.observed.network[1].response.datos.partidos = item.observed.network[1].response.datos.partidos.slice(0, 1);
  assert.throws(() => verifyCase(item), /changed the displayed/);
  item.observed.network[0].request.fixture_context.despues_de = '2026-11-08T17:00Z';
  item.observed.network[1].response.datos.partidos[0].fecha_iso = '2026-11-08T17:00Z';
  assert.doesNotThrow(() => verifyCase(item));
});
