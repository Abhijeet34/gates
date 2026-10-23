// The browser half of run/ui-gates.py, which owns the contract and judges what
// this prints: one JSON line naming the versions it ran with, and the axe
// violations or the screenshot's path.
//
// Usage: node ui-gates.mjs <node_modules> axe <url> <tags>
//        node ui-gates.mjs <node_modules> shot <url> <out.png>
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [modules, verb, url, arg] = process.argv.slice(2);
const version = name => JSON.parse(readFileSync(join(modules, name, 'package.json'), 'utf8')).version;
let playwright;
try {
  playwright = await import(pathToFileURL(join(modules, 'playwright', 'index.mjs')).href);
} catch (e) {
  console.error(`missing-tool: playwright does not load from ${modules}: ${e.message}`);
  process.exit(3);
}

// One line for ui-gates.py to quote: a missing headless shell is a missing tool.
const fail = e => {
  const first = String(e.message).split('\n')[0];
  console.error(/Executable doesn't exist/.test(first) ? `missing-tool: ${first}` : first);
  process.exit(/Executable doesn't exist/.test(first) ? 3 : 1);
};
const browser = await playwright.chromium.launch({ chromiumSandbox: true }).catch(fail);
try {
  // The viewport fac browse opens with, so a baseline means the same here and there.
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'load' });
  const out = { versions: { playwright: version('playwright'), browser: browser.version() } };
  if (verb === 'axe') {
    out.versions['axe-core'] = version('axe-core');
    const axe = readFileSync(createRequire(join(modules, 'x.js')).resolve('axe-core/axe.min.js'), 'utf8');
    const opts = { runOnly: { type: 'tag', values: arg.split(',') }, resultTypes: ['violations'] };
    // Evaluated over the debugger protocol, as fac browse does, so a page's
    // Content-Security-Policy cannot keep axe out.
    const r = await page.evaluate(`${axe}\n;window.axe.run(document, ${JSON.stringify(opts)})`);
    out.violations = r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length }));
  } else {
    writeFileSync(arg, await page.screenshot({ animations: 'disabled', caret: 'hide' }));
    out.screenshot = arg;
  }
  console.log(JSON.stringify(out));
} catch (e) {
  await browser.close();
  fail(e);
}
await browser.close();
