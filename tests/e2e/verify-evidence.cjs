/* Independent assertions over retained real-model evidence; no inference or network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');

function expectedTeam(id) {
  if (/^(atleti|atletico)/.test(id) || id === 'following-three-worded') return 'Atlético Madrid';
  if (id.startsWith('barca')) return 'Barcelona';
  if (id.startsWith('sevilla')) return 'Sevilla';
  if (id.startsWith('valencia')) return 'Valencia';
  return 'Real Madrid';
}

function verifyCase(item) {
  const evidence = item.observed;
  assert.ok(evidence, 'Missing completed turn evidence.');
  const calls = evidence.network.filter(n => n.url === '/api/tool');
  if (item.expected === 'scope') {
    assert.equal(calls.length, 0, 'Unsupported query executed a data tool.');
    return;
  }
  const successful = calls.filter(call => call.response.ok);
  assert.equal(successful.length, 1);
  const call = successful[0];
  const args = call.request.args;
  const data = call.response.datos;
  if (typeof item.expected !== 'number' && item.expected !== 'ultimo_resultado') return;
  const focal = expectedTeam(item.id);
  assert.equal(args.equipo, focal, 'Executed query changed the requested focal club.');
  if (item.id === 'clasico-result') assert.equal(args.rival, 'Barcelona');
  else assert.ok(!args.rival, 'A single-club request acquired an unrequested opponent filter.');
  if (item.expected === 'ultimo_resultado') {
    assert.ok([data.local, data.visitante].includes(focal));
    if (item.id === 'clasico-result') assert.deepEqual(new Set([data.local, data.visitante]), new Set(['Barcelona', 'Real Madrid']));
    return;
  }
  assert.equal(data.partidos.length, item.expected);
  const stamps = data.partidos.map(match => {
    assert.ok([match.local, match.visitante].includes(focal), 'Returned fixture omits requested club.');
    const stamp = Date.parse(match.fecha_iso);
    assert.ok(Number.isFinite(stamp), 'Fixture timestamp is missing or invalid.');
    return stamp;
  });
  for (let i = 1; i < stamps.length; i++) assert.ok(stamps[i] > stamps[i - 1], 'Fixtures are not strictly chronological.');
  const ask = evidence.network.find(n => n.url === '/api/ask');
  const anchor = ask?.request.fixture_context?.despues_de;
  if (/^confirmation/.test(item.id)) {
    assert.ok(anchor, 'Confirmation has no displayed fixture anchor.');
    assert.equal(stamps[0], Date.parse(anchor), 'Confirmation changed the displayed fixture.');
  } else if (/^following|^next-plus|^after-event/.test(item.id)) {
    assert.ok(anchor, 'Follow-up has no previous displayed fixture anchor.');
    assert.ok(stamps.every(stamp => stamp > Date.parse(anchor)), 'Follow-up repeated or preceded the displayed event.');
  }
  if (item.id === 'after-date') {
    const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' });
    assert.ok(stamps.every(stamp => formatter.format(new Date(stamp)) > '2026-11-01'), 'Explicit calendar boundary was ignored.');
  }
  const prose = evidence.text.at(-1) || '';
  for (const match of data.partidos) {
    assert.ok(prose.includes(match.local) && prose.includes(match.visitante) && prose.includes(match.dia),
      'Model prose, independently of cards, omits a fixture.');
  }
}

module.exports = { verifyCase };
if (require.main === module) {
  const report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const checks = report.cases.map(item => {
    try {
      verifyCase(item);
      return { id: item.id, status: item.status === 'passed' ? 'passed' : 'failed', priorFailure: item.error };
    } catch (error) { return { id: item.id, status: 'failed', error: error.message }; }
  });
  const output = { checked: checks.length, passed: checks.filter(c => c.status === 'passed').length, checks };
  console.log(JSON.stringify(output, null, 2));
  process.exitCode = output.passed === output.checked ? 0 : 1;
}
