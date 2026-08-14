#!/usr/bin/env node
'use strict';

/*
 * layout-probe.js -- measures, in REAL headless Chrome, whether the sticky
 * period-filter bar visually covers the trend chart's mode tabs while the
 * page scrolls.
 *
 * WHY THIS EXISTS AND IS NOT A TEST: jsdom (which the rendering test suite
 * uses) performs no layout at all and does not resolve `var()`. During the
 * period-filters phase a sticky-positioning fix shipped that jsdom waved
 * through as fine -- and headless Chrome later measured it as completely
 * inert: byte-identical in behaviour to the broken build, 29px of 29
 * covered at scrollY=44. A claim about visual overlap needs a real
 * rendering engine, and a real engine is too heavy and too
 * environment-dependent (needs an installed Chrome, spawns a subprocess,
 * takes real wall-clock seconds) to gate the test suite on. So it lives
 * here, run by hand, and pytest does NOT run it -- see scripts/README.md.
 *
 * THE FALSE GREEN THIS PROBE GUARDS AGAINST: every session group on the
 * dashboard is born collapsed. With every group collapsed the document is
 * only a few hundred pixels tall and the trend chart's mode tabs never
 * leave their starting position -- the page never scrolls far enough for
 * the sticky behaviour to engage at all. A naive probe that only checked
 * "does the filter rect overlap the tabs rect" would report 0px overlap
 * in that state, which is true and meaningless: the tabs were never
 * exercised, not proven safe. This probe (1) expands a group so the
 * document is genuinely taller than the viewport, (2) after scrolling to
 * each sampled offset, confirms the browser actually reached that
 * scrollY (catching a page that silently clamped because it wasn't tall
 * enough), and (3) confirms the mode tabs are actually on screen at that
 * offset before treating a 0px-overlap reading as meaningful. Any of
 * those checks failing is reported as a loud FAIL, not folded into a
 * silent "0px covered".
 *
 * Usage:
 *   node scripts/layout-probe.js <path-to-dashboard.html>
 *
 * Exit codes:
 *   0  every sampled offset was on screen and uncovered.
 *   1  the probe ran to completion and found a real problem: the mode
 *      tabs were covered, or were not verifiably on screen, at one or
 *      more sampled offsets.
 *   2  the probe itself could not run (no dashboard path given, no
 *      Chrome found, Chrome did not start, navigation failed, ...). This
 *      is a tooling failure, distinct from "the probe ran and the layout
 *      is broken".
 */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

// Scroll offsets to sample, in px. Centered on --period-filter-height
// (44px, dashboard_html.py) because that is exactly the offset at which
// a regressed `top` on `body > .period-block` starts sticking the trend
// wrapper directly under the filter bar instead of below it -- see the
// CSS comment above `body > .period-block` in dashboard_html.py. The
// sweep extends a little past it so a one-off exact-pixel coincidence
// isn't mistaken for "always safe".
const SCROLL_OFFSETS = [0, 22, 44, 66, 88, 110];

// How far past the last sampled offset the page must genuinely be able
// to scroll for the sweep to mean anything. Below this, `window.scrollTo`
// would silently clamp and every reading downstream would be measuring a
// page that never actually moved -- exactly the false green this probe
// exists to catch.
const REQUIRED_SCROLLABLE_PX = SCROLL_OFFSETS[SCROLL_OFFSETS.length - 1];

// `.trend-mode-tabs` alone is NOT enough: dashboard_html.py renders one
// trend period-block PER PERIOD (5 total, see render_html), four of them
// carrying the `hidden` attribute (CSS: `.period-block[hidden] { display:
// none }`) and only the default period's block visible. A plain
// `document.querySelector('.trend-mode-tabs')` returns the FIRST one in
// DOM order, which -- because "tudo" (the default, the only one NOT
// hidden) is rendered LAST among the five periods -- is very often a
// HIDDEN copy: a real, zero-size, `display:none` element that reads as
// "not covered" for the wrong reason. This bit the probe during its own
// development (found by this exact symptom: every sampled offset came
// back with a 0x0 rect). Scoping to the one period-block that is not
// hidden is what makes "on screen" mean the same thing here as it does
// to a person looking at the actual rendered page.
const TABS_SELECTOR = '.period-block:not([hidden]) .trend-mode-tabs';

// Deliberately shorter than a full laptop viewport. The test fixture
// dashboard (tests/fixtures/build_fixture.py, 3 groups / 4 sessions) is
// only ~650px tall even with every group expanded, and
// document.documentElement.scrollHeight reports the VIEWPORT height, not
// the content height, whenever content is shorter than the viewport --
// an 800px-tall viewport against ~650px of content silently produces
// zero scrollable range and the sweep below could never reach any of its
// offsets. A real dashboard with actual history is thousands of pixels
// tall regardless of viewport, so this only matters for small fixtures
// like the one this probe is proven against in Step 2 of the brief.
const VIEWPORT = { width: 1280, height: 480 };

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);

class ProbeSetupError extends Error {}

function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new ProbeSetupError(
    `No Chrome/Chromium binary found. Checked:\n  ${CHROME_CANDIDATES.join('\n  ')}\n` +
    'Set CHROME_PATH to point at one explicitly.'
  );
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForDevtools(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return;
    } catch (err) {
      lastError = err;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new ProbeSetupError(
    `Chrome's DevTools endpoint on port ${port} never came up within ${timeoutMs}ms.` +
    (lastError ? ` Last error: ${lastError.message}` : '')
  );
}

// A minimal Chrome DevTools Protocol client: sends {id, method, params},
// resolves the matching {id, result}/{id, error} reply. No third-party
// dependency (puppeteer/playwright) is installed for this -- the
// surface this script needs (navigate, evaluate JS, read the result) is
// small enough that a raw CDP connection over the WebSocket/fetch
// globals already built into Node is less to maintain than a new
// devDependency for an occasionally-run script.
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
  }

  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', () => reject(new ProbeSetupError(`Could not open CDP WebSocket to ${wsUrl}`)), { once: true });
    });
    const cdp = new CDP(ws);
    ws.addEventListener('message', (ev) => cdp._onMessage(ev.data));
    return cdp;
  }

  _onMessage(raw) {
    const msg = JSON.parse(raw);
    if (msg.id === undefined) return; // an event we don't listen for
    const waiter = this.pending.get(msg.id);
    if (!waiter) return;
    this.pending.delete(msg.id);
    if (msg.error) waiter.reject(new Error(`CDP error on id ${msg.id}: ${msg.error.message}`));
    else waiter.resolve(msg.result);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    this.ws.close();
  }
}

// Runs `expression` in the page and returns its value. The expression
// must be a self-invoking function so Runtime.evaluate has something to
// serialize back with returnByValue.
async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: false,
  });
  if (result.exceptionDetails) {
    const text = result.exceptionDetails.exception && result.exceptionDetails.exception.description
      ? result.exceptionDetails.exception.description
      : JSON.stringify(result.exceptionDetails);
    throw new Error(`Page-side JS threw during the probe:\n${text}`);
  }
  return result.result.value;
}

async function runProbe(dashboardPath) {
  const absPath = path.resolve(dashboardPath);
  if (!fs.existsSync(absPath)) {
    throw new ProbeSetupError(`No such file: ${absPath}`);
  }

  const chromePath = findChrome();
  const port = await freePort();

  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      '--disable-gpu',
      '--disable-extensions',
      '--hide-scrollbars',
      '--force-device-scale-factor=1',
      `--remote-debugging-port=${port}`,
      'about:blank',
    ],
    { stdio: 'ignore' }
  );

  let chromeExitError = null;
  chrome.on('error', (err) => { chromeExitError = err; });

  try {
    await waitForDevtools(port, 15000);
    if (chromeExitError) throw new ProbeSetupError(`Chrome failed to launch: ${chromeExitError.message}`);

    // PUT, not GET: Chrome added a CSRF guard that rejects a bare GET to
    // /json/new with 405. This tripped the probe during development --
    // recorded so it isn't "fixed" back to GET later.
    const newTabRes = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
    if (!newTabRes.ok) {
      throw new ProbeSetupError(`Could not open a new tab via DevTools (HTTP ${newTabRes.status})`);
    }
    const target = await newTabRes.json();
    if (!target.webSocketDebuggerUrl) {
      throw new ProbeSetupError(`DevTools /json/new response had no webSocketDebuggerUrl: ${JSON.stringify(target)}`);
    }

    const cdp = await CDP.connect(target.webSocketDebuggerUrl);
    try {
      await cdp.send('Page.enable');
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: VIEWPORT.width,
        height: VIEWPORT.height,
        deviceScaleFactor: 1,
        mobile: false,
      });

      const loaded = new Promise((resolve) => {
        cdp.ws.addEventListener('message', function onMsg(ev) {
          const msg = JSON.parse(ev.data);
          if (msg.method === 'Page.loadEventFired') {
            cdp.ws.removeEventListener('message', onMsg);
            resolve();
          }
        });
      });
      await cdp.send('Page.navigate', { url: `file://${absPath}` });
      const navigationTimedOut = await Promise.race([
        loaded.then(() => false),
        new Promise((r) => setTimeout(() => r(true), 15000)),
      ]);
      if (navigationTimedOut) {
        throw new ProbeSetupError(`Page.loadEventFired never arrived for ${absPath} within 15s`);
      }

      return await measure(cdp);
    } finally {
      cdp.close();
    }
  } finally {
    chrome.kill();
  }
}

async function measure(cdp) {
  // Step 1: expand every group so the document is genuinely taller than
  // the viewport. Every group is born with class "collapsed" (see
  // render_group_header in dashboard_html.py) -- clicking a header runs
  // the page's own toggleGroup(), the same as a person would. Expanding
  // ALL of them (not just one) is a deliberate margin of safety: a small
  // fixture with only a couple of groups needs every one of them open to
  // clear REQUIRED_SCROLLABLE_PX at all, and the brief's own false-green
  // scenario is specifically about a page that never got tall enough.
  const expanded = await evaluate(cdp, `(() => {
    const headers = Array.from(document.querySelectorAll('.group-header.collapsed'));
    if (headers.length === 0) return { ok: false, reason: 'no .group-header.collapsed found on the page' };
    headers.forEach((h) => h.click());
    return { ok: true, ids: headers.map((h) => h.id) };
  })()`);
  if (!expanded.ok) {
    throw new ProbeSetupError(
      `Could not expand any session group: ${expanded.reason}. Without an expanded group the ` +
      'document may be too short for the sweep below to mean anything -- see the false-green ' +
      'note at the top of this file.'
    );
  }

  // Step 2: confirm the document is actually tall enough to scroll
  // through the whole sweep, and confirm the two elements the sweep
  // depends on exist at all. Both checks exist so a failure downstream
  // in the sweep can never be misread as "0px overlap" when the real
  // cause was "the page never scrolled" or "the elements aren't there".
  const geometry = await evaluate(cdp, `(() => {
    const filterEl = document.querySelector('.period-filter');
    const tabsEl = document.querySelector(${JSON.stringify(TABS_SELECTOR)});
    return {
      hasFilter: !!filterEl,
      hasTabs: !!tabsEl,
      scrollHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
    };
  })()`);
  if (!geometry.hasFilter || !geometry.hasTabs) {
    throw new ProbeSetupError(
      `Missing element(s) on the page: .period-filter present=${geometry.hasFilter}, ` +
      `.trend-mode-tabs present=${geometry.hasTabs}. Cannot measure overlap without both.`
    );
  }
  const maxScrollable = geometry.scrollHeight - geometry.viewportHeight;
  if (maxScrollable < REQUIRED_SCROLLABLE_PX) {
    throw new ProbeSetupError(
      `The page can only scroll ${maxScrollable}px (scrollHeight=${geometry.scrollHeight}, ` +
      `viewport=${geometry.viewportHeight}), but the sweep needs at least ${REQUIRED_SCROLLABLE_PX}px. ` +
      'Expanding a group did not make the page tall enough -- this is exactly the false green ' +
      '("~376px tall, trend container never enters the viewport") this probe is built to refuse ' +
      'to paper over. A dashboard with only one tiny group, or a dashboard whose fixture has too ' +
      'few sessions, can hit this legitimately; point the probe at a dashboard with more data.'
    );
  }

  // Step 3: sweep the scroll offsets and read real, post-layout
  // getBoundingClientRect() values at each one.
  const readings = [];
  for (const y of SCROLL_OFFSETS) {
    const reading = await evaluate(cdp, `(() => {
      window.scrollTo(0, ${y});
      const filterEl = document.querySelector('.period-filter');
      const tabsEl = document.querySelector(${JSON.stringify(TABS_SELECTOR)});
      const f = filterEl.getBoundingClientRect();
      const t = tabsEl.getBoundingClientRect();
      return {
        requestedY: ${y},
        actualY: window.scrollY,
        filterRect: { top: f.top, bottom: f.bottom },
        tabsRect: { top: t.top, bottom: t.bottom, height: t.height },
        viewportHeight: window.innerHeight,
      };
    })()`);
    readings.push(reading);
  }

  // Step 4: judge each reading. A reading only gets to claim "0px
  // overlap, PASS" if it can first show the tabs were genuinely on
  // screen and the browser genuinely reached the requested scrollY.
  // Everything else is a loud FAIL with the reason attached, never a
  // silently-passing 0.
  const verdicts = readings.map((r) => {
    const reachedOffset = Math.abs(r.actualY - r.requestedY) <= 1;
    if (!reachedOffset) {
      return {
        ...r,
        status: 'FAIL',
        reason: `browser scrolled to ${r.actualY}px, not the requested ${r.requestedY}px -- the page clamped short of this offset`,
      };
    }
    const onScreen = r.tabsRect.height > 0 && r.tabsRect.bottom > 0 && r.tabsRect.top < r.viewportHeight;
    if (!onScreen) {
      return {
        ...r,
        status: 'FAIL',
        reason: `mode tabs are NOT on screen at scrollY=${r.actualY} (rect top=${r.tabsRect.top}, bottom=${r.tabsRect.bottom}, height=${r.tabsRect.height}) -- an overlap reading here would be meaningless, not "0px covered"`,
      };
    }
    const overlapTop = Math.max(r.filterRect.top, r.tabsRect.top);
    const overlapBottom = Math.min(r.filterRect.bottom, r.tabsRect.bottom);
    const overlapPx = Math.max(0, overlapBottom - overlapTop);
    if (overlapPx > 0) {
      return {
        ...r,
        status: 'FAIL',
        overlapPx,
        reason: `${overlapPx.toFixed(1)}px of ${r.tabsRect.height.toFixed(1)}px covered by .period-filter at scrollY=${r.actualY}`,
      };
    }
    return { ...r, status: 'PASS', overlapPx: 0, reason: `0px covered, tabs on screen (top=${r.tabsRect.top.toFixed(1)}, bottom=${r.tabsRect.bottom.toFixed(1)})` };
  });

  return { expandedGroups: expanded.ids, maxScrollable, verdicts };
}

function report({ expandedGroups, maxScrollable, verdicts }) {
  console.log(`Expanded groups: ${expandedGroups.join(', ')}`);
  console.log(`Document scrollable range: ${maxScrollable}px (sweep needs >= ${REQUIRED_SCROLLABLE_PX}px)`);
  console.log('');
  console.log('scrollY  status  detail');
  for (const v of verdicts) {
    console.log(`${String(v.requestedY).padStart(4)}px   ${v.status.padEnd(4)}    ${v.reason}`);
  }
  console.log('');
  const failures = verdicts.filter((v) => v.status === 'FAIL');
  if (failures.length > 0) {
    console.log(`FAIL: ${failures.length} of ${verdicts.length} sampled offsets are broken.`);
    return 1;
  }
  console.log(`PASS: all ${verdicts.length} sampled offsets kept the mode tabs on screen and uncovered.`);
  return 0;
}

async function main() {
  const dashboardPath = process.argv[2];
  if (!dashboardPath) {
    console.error('Usage: node scripts/layout-probe.js <path-to-dashboard.html>');
    process.exit(2);
  }
  try {
    const result = await runProbe(dashboardPath);
    process.exit(report(result));
  } catch (err) {
    if (err instanceof ProbeSetupError) {
      console.error(`SETUP ERROR: ${err.message}`);
      process.exit(2);
    }
    console.error(`UNEXPECTED ERROR: ${err.stack || err.message}`);
    process.exit(2);
  }
}

main();
