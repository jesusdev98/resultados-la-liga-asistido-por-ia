// Deterministic DOM integration, not proof of browser/GPU/model behavior.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');
const home = process.env.LALIGA_TEST_HOME || 'C:/Users/jesus/AppData/Local/Temp/opencode/prueba-deepseek-e2e';
const { JSDOM } = require(path.join(home, 'node_modules/jsdom'));
const { createInstrumenter } = require(path.join(home, 'node_modules/istanbul-lib-instrument'));
const { createCoverageMap } = require(path.join(home, 'node_modules/istanbul-lib-coverage'));
const root = path.resolve(__dirname, '../..');
const web = path.join(root, 'web');
const coverage = createCoverageMap({});

// Seed the entire production JavaScript inventory, even modules never imported.
for (const name of fs.readdirSync(web).filter(name => name.endsWith('.js'))) {
  const filename = path.join(web, name);
  const instrumenter = createInstrumenter({ esModules: true, produceSourceMap: false });
  instrumenter.instrumentSync(fs.readFileSync(filename, 'utf8'), filename);
  coverage.addFileCoverage(instrumenter.lastFileCoverage());
}

function createHarness(options = {}) {
  const html = options.html ?? fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'http://localhost:8000/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = options.fetch || (async () => { throw new Error('Unexpected test request'); });
  window.console = options.console || { log() {}, info() {}, warn() {}, error() {}, debug() {} };
  window.structuredClone = structuredClone;
  const context = options.browserless
    ? vm.createContext({ document: window.document, console: window.console })
    : dom.getInternalVMContext();
  const modules = new Map();
  const stubs = options.modules || {};

  async function importModule(specifier, referencing) {
    const filename = specifier.startsWith('.')
      ? path.resolve(referencing ? path.dirname(referencing.identifier) : web, specifier)
      : specifier;
    const key = Object.hasOwn(stubs, specifier) ? specifier : filename;
    if (modules.has(key)) return modules.get(key);
    if (Object.hasOwn(stubs, key)) {
      const values = stubs[key];
      const mod = new vm.SyntheticModule(Object.keys(values), function () {
        for (const [name, value] of Object.entries(values)) this.setExport(name, value);
      }, { context, identifier: key });
      modules.set(key, mod);
      await mod.link(() => { throw new Error('Synthetic modules have no imports'); });
      await mod.evaluate();
      return mod;
    }
    if (!filename.startsWith(web + path.sep)) throw new Error(`Unbound import: ${specifier}`);
    const instrumenter = createInstrumenter({ esModules: true, produceSourceMap: false });
    const source = instrumenter.instrumentSync(fs.readFileSync(filename, 'utf8'), filename);
    const mod = new vm.SourceTextModule(source, {
      context, identifier: filename,
      initializeImportMeta(meta) { meta.url = pathToFileURL(filename).href; },
      importModuleDynamically: importModule,
    });
    modules.set(key, mod);
    await mod.link(importModule);
    await mod.evaluate();
    return mod;
  }

  return {
    window, document: window.document,
    async load(name) { return (await importModule('./' + name)).namespace; },
    async waitFor(predicate, timeout = 2000) {
      const deadline = Date.now() + timeout;
      while (!predicate()) {
        if (Date.now() >= deadline) throw new Error('DOM condition timed out');
        await new Promise(resolve => setTimeout(resolve, 5));
      }
    },
    close() {
      if (context.__coverage__) coverage.merge(context.__coverage__);
      window.close();
    },
  };
}

process.on('exit', () => {
  const output = path.join(home, 'frontend-coverage');
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, `${process.env.LALIGA_COVERAGE_NAME || process.pid}.json`), JSON.stringify(coverage.toJSON()));
});

module.exports = { createHarness, home, root };
