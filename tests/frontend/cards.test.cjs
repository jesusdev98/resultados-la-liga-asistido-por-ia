const test = require('node:test');
const assert = require('node:assert/strict');
const { createHarness } = require('./harness.cjs');

test('standalone card rendering preserves guards, safe logos, and source text', async () => {
  const h = createHarness();
  try {
    const api = await h.load('match-cards.js');
    const container = h.document.createElement('section');
    h.document.body.append(container);
    for (const invalid of [null, undefined, 'invalid', 4]) {
      api.appendFixtureCard(container, invalid);
      api.appendResultCard(container, invalid);
    }
    assert.equal(container.childElementCount, 0);
    api.appendFixtureCard(container, { local: '<Madrid>', visitante: 'Valencia', dia: 'Source day', hora: '21:00', horario_confirmado: true });
    assert.equal(container.querySelector('img'), null);
    assert.match(container.textContent, /<Madrid>.*Valencia.*Source day.*21:00.*confirmados/);
    assert.equal(container.querySelector('Madrid'), null);
    api.appendFixtureCard(container, {}, {});
    assert.match(container.lastChild.textContent, /Fecha por confirmar.*provisional/);
    const logos = { Madrid: 'https://a.espncdn.com/madrid.png', Valencia: 'http://bad.invalid/x' };
    api.appendFixtureCard(container, { local: 'Madrid', visitante: 'Valencia', horario_confirmado: false }, logos);
    let image = container.lastChild.querySelector('img');
    assert.equal(image.referrerPolicy, 'no-referrer');
    image.dispatchEvent(new h.window.Event('error'));
    assert.equal(container.lastChild.querySelector('img'), null);
    api.appendResultCard(container, { local: 'Madrid', visitante: 'Valencia', goles_local: 2, goles_visitante: 1, dia: 'Source date' }, logos);
    assert.match(container.lastChild.textContent, /Madrid.*Valencia.*2-1.*Source date.*Finalizado/);
    image = container.lastChild.querySelector('img');
    image.dispatchEvent(new h.window.Event('error'));
    assert.equal(container.lastChild.querySelector('img'), null);
    api.appendResultCard(container, { goles_local: 0, goles_visitante: 0 });
    assert.match(container.lastChild.textContent, /0-0.*Fecha no disponible/);
    for (const value of [null, 1, '', 'invalid', 'http://a.espncdn.com/a', 'https://evil.invalid/a', 'https://u@a.espncdn.com/a', 'https://:p@a.espncdn.com/a', 'https://a.espncdn.com:444/a']) {
      assert.equal(api.safeTeamLogo(value), '');
    }
    assert.equal(api.safeTeamLogo('https://a.espncdn.com:443/a'), 'https://a.espncdn.com/a');
  } finally { h.close(); }
});
