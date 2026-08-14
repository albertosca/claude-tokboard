// Drives the REAL generated dashboard.html in jsdom: executes its own
// <script>, calls its own toggleGroup / selectPeriod / showSession, and asks
// getComputedStyle what is actually visible. Every assertion is about
// BEHAVIOUR - "the markup contains data-periods" passes whether the filter
// works or not, which is exactly how the inert .period-hidden rule shipped.
//
// Expectations arrive on argv[2] as {periods: {<id>: {session_ids,
// token_total, legend}}, default_period}, computed by the Python side from
// metrics_core over the fixture's own records. Nothing in this file knows
// what the right answer is; it only knows how to interrogate the page.
const fs = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
if (!HTML) {
  console.error("DASHBOARD env var is required (path to the generated dashboard.html)");
  process.exit(2);
}
const EXPECT = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const PERIOD_IDS = Object.keys(EXPECT.periods);

const dom = new JSDOM(fs.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

let styleCache = new Map();
function displayOf(el) {
  if (styleCache.has(el)) return styleCache.get(el);
  const d = window.getComputedStyle(el).display;
  styleCache.set(el, d);
  return d;
}
// Visible = neither this element nor any ancestor computes to display:none.
// Ancestors matter for the composition legend, which is hidden by its
// wrapping .period-block rather than by a class of its own.
function isVisible(el) {
  let node = el;
  while (node && node.nodeType === 1) {
    if (displayOf(node) === "none") return false;
    node = node.parentElement;
  }
  return true;
}
function freshStyles() {
  styleCache = new Map();
}

// jsdom does no layout, so scrollWidth is permanently 0 and any assertion
// phrased as "scrollLeft reached scrollWidth" would pass whether the page
// scrolls or not. Stubbing scrollWidth to a non-zero value turns it into an
// instrument that can actually fail: scrollLeft is real storage in jsdom, so
// the page's own assignment is observable.
const SCROLL_WIDTH = 4242;
Object.defineProperty(window.Element.prototype, "scrollWidth", {
  get() { return SCROLL_WIDTH; },
  configurable: true,
});

const headers = Array.from(document.querySelectorAll(".group-header"));
const rows = Array.from(document.querySelectorAll(".session-row[data-periods]"));
const legendBlocks = Array.from(document.querySelectorAll(".session-list > .period-block"));
const panels = Array.from(document.querySelectorAll(".detail-panel[data-periods]"));
const allTrendScrolls = Array.from(document.querySelectorAll(".trend-scroll"));

// session id -> the group index its row was rendered into. Group membership
// is not what is under test here (period scoping is), so reading it off the
// markup is fair: it lets the expected session ids be turned into an
// expected set of group headers and an expected count per header.
const groupOfSession = new Map();
rows.forEach((row) => {
  groupOfSession.set(row.id.replace(/^row-/, ""), row.dataset.group);
});

function groupCounts(sessionIds) {
  const counts = {};
  sessionIds.forEach((id) => {
    const group = groupOfSession.get(id);
    if (group === undefined) return;
    counts[group] = (counts[group] || 0) + 1;
  });
  return counts;
}

const UNITS = { k: 1e3, M: 1e6, B: 1e9, T: 1e12 };
// format_tokens rounds to one decimal above 1000, so a legend of formatted
// chips can only reconstruct the raw total to within half of the last digit
// it printed. The tolerance is that rounding and nothing more: it is a few
// hundred tokens wide at most, so a legend quoting the wrong PERIOD's total
// misses by orders of magnitude and still fails loudly.
function parseFormatted(text) {
  const match = /^(-?[\d.]+)([kMBT])?$/.exec((text || "").trim());
  if (!match) return null;
  const unit = match[2] ? UNITS[match[2]] : 1;
  return { value: parseFloat(match[1]) * unit, tolerance: match[2] ? unit * 0.05 : 0 };
}

function toggleTo(header, wantCollapsed) {
  if (header.classList.contains("collapsed") !== wantCollapsed) {
    window.toggleGroup(parseInt(header.id.replace("group-", ""), 10));
  }
}
function expandAllGroups() {
  headers.forEach((h) => toggleTo(h, false));
}
function collapseAllGroups() {
  headers.forEach((h) => toggleTo(h, true));
}
function resetScroll() {
  allTrendScrolls.forEach((el) => { el.scrollLeft = 0; });
}
function closeAllPanels() {
  panels.forEach((panel) => {
    window.closeDetail(panel.id.replace(/^detail-/, ""));
  });
}

function measure(periodId) {
  freshStyles();
  const visibleRows = rows.filter(isVisible);
  const visibleHeaders = headers.filter(isVisible);
  const visibleLegends = legendBlocks.filter(isVisible);
  const legend = {};
  if (visibleLegends.length === 1) {
    visibleLegends[0].querySelectorAll(".legend-total").forEach((el) => {
      legend[el.getAttribute("data-series")] = el.textContent.trim();
    });
  }
  // A header is only allowed on screen if at least one of its own sessions
  // belongs to the period, and the count it prints has to describe what the
  // reader can actually see - "29 group headers quoting the all-time total
  // under ontem" is the exact defect this pair of checks exists for.
  const headerCounts = {};
  let headersWithoutCount = 0;
  visibleHeaders.forEach((h) => {
    const span = Array.from(h.querySelectorAll(".group-count")).find(isVisible);
    const parsed = span && /^\((\d+) sess/.exec(span.textContent.trim());
    if (!parsed) {
      headersWithoutCount += 1;
      return;
    }
    headerCounts[h.id.replace("group-", "")] = parseInt(parsed[1], 10);
  });
  const activeScrolls = Array.from(
    document.querySelectorAll('.period-block[data-period="' + periodId + '"] .trend-scroll')
  );
  return {
    sessionIds: visibleRows.map((el) => el.id.replace(/^row-/, "")).sort(),
    rowsNotFlex: visibleRows
      .filter((el) => displayOf(el) !== "flex")
      .map((el) => el.id + "=" + displayOf(el))
      .sort(),
    headerGroups: visibleHeaders.map((h) => h.id.replace("group-", "")).sort(),
    headerCounts,
    headersWithoutCount,
    legendBlocks: visibleLegends.length,
    legend,
    visiblePanelIds: panels.filter(isVisible).map((el) => el.id.replace(/^detail-/, "")).sort(),
    trendScrolls: activeScrolls.length,
    trendScrollsAtNewest: activeScrolls.filter((el) => el.scrollLeft === SCROLL_WIDTH).length,
  };
}

const failures = [];
let assertionCount = 0;
function check(label, actual, expected) {
  assertionCount += 1;
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures.push(
      `${label}\n      got:      ${JSON.stringify(actual)}\n      expected: ${JSON.stringify(expected)}`
    );
  }
  console.log(`  ${ok ? "PASS" : "FAIL"} ${label}: ${JSON.stringify(actual)}`);
}

function section(title) {
  console.log(`\n${title}`);
  return assertionCount;
}
function endSection(title, startedAt) {
  console.log(`  -> ${title}: ${assertionCount - startedAt} assertions`);
}

// Shared by the initial-state check and every per-period check, so a defect
// baked into the delivered markup and a defect in selectPeriod are held to
// exactly the same standard.
function checkAgainst(periodId, got) {
  const want = EXPECT.periods[periodId];
  const wantGroups = Object.keys(groupCounts(want.session_ids)).sort();
  check("visible session ids", got.sessionIds, want.session_ids);
  check("visible rows not computing display:flex", got.rowsNotFlex, []);
  check("visible group headers", got.headerGroups, wantGroups);
  check("session count printed by each visible header", got.headerCounts, groupCounts(want.session_ids));
  check("visible headers with no visible count", got.headersWithoutCount, 0);
  check("visible composition legends", got.legendBlocks, 1);
  check("composition legend chips", got.legend, want.legend);
  const chips = Object.keys(want.legend).map((series) => parseFormatted(got.legend[series]));
  check("composition legend chips all parse as numbers", chips.every(Boolean), true);
  if (chips.every(Boolean)) {
    const sum = chips.reduce((acc, chip) => acc + chip.value, 0);
    const tolerance = chips.reduce((acc, chip) => acc + chip.tolerance, 0);
    check(
      `composition legend sums to the period's token total (${want.token_total} +/- ${tolerance})`,
      Math.abs(sum - want.token_total) <= tolerance,
      true
    );
  }
}

// The page AS DELIVERED, before any click: selectPeriod repairs a lot of
// initial-state damage the moment it runs, so measuring only after a click
// hides any defect baked into the markup itself. During the fix wave a
// canary reported NOTHING FIRED for exactly this reason.
{
  const started = section(
    `[initial] page as delivered, default period '${EXPECT.default_period}', nothing clicked`
  );
  const initial = measure(EXPECT.default_period);
  const want = EXPECT.periods[EXPECT.default_period];
  // Every group ships collapsed, so no row is on screen yet - but the
  // headers, their counts and the legend are all already period-scoped.
  check("visible session rows while collapsed", initial.sessionIds, []);
  check("visible group headers on load", initial.headerGroups, Object.keys(groupCounts(want.session_ids)).sort());
  check("session count printed by each header on load", initial.headerCounts, groupCounts(want.session_ids));
  check("visible headers with no visible count on load", initial.headersWithoutCount, 0);
  check("visible composition legends on load", initial.legendBlocks, 1);
  check("composition legend chips on load", initial.legend, want.legend);
  check("detail panels open on load", initial.visiblePanelIds, []);
  const activeChips = Array.from(document.querySelectorAll(".period-chip.active"))
    .map((chip) => chip.dataset.period);
  check("period chips marked active on load", activeChips, [EXPECT.default_period]);
  endSection("initial state", started);
}

function runPeriod(periodId, order) {
  const started = section(`[${order}] period=${periodId}`);
  if (order === "period-then-expand") {
    collapseAllGroups();
    resetScroll();
    window.selectPeriod(periodId);
    expandAllGroups();
  } else {
    expandAllGroups();
    resetScroll();
    window.selectPeriod(periodId);
  }
  const got = measure(periodId);
  checkAgainst(periodId, got);
  // Every trend-scroll in the newly-visible block must have been scrolled to
  // its newest data by selectPeriod itself, from a reset of 0. Guarded by a
  // non-zero count: 0 === 0 would pass on a page with no trend chart at all.
  check("trend-scroll elements inside the active period block", got.trendScrolls > 0, true);
  check("newly-visible trend blocks scrolled to newest", got.trendScrollsAtNewest, got.trendScrolls);
  endSection(`period=${periodId} [${order}]`, started);
}

for (const order of ["expand-then-period", "period-then-expand"]) {
  for (const periodId of PERIOD_IDS) {
    runPeriod(periodId, order);
  }
}

// A detail panel left open for a session that just left the period has to
// leave with it, instead of describing a row nobody can see anymore.
{
  const started = section("[detail] an open panel follows its session out of the period");
  for (const periodId of PERIOD_IDS) {
    const want = EXPECT.periods[periodId].session_ids;
    const everySession = EXPECT.periods[EXPECT.default_period].session_ids;
    const insider = want[0];
    const outsider = everySession.find((id) => want.indexOf(id) === -1);
    if (insider) {
      closeAllPanels();
      window.selectPeriod(EXPECT.default_period);
      window.showSession(insider);
      window.selectPeriod(periodId);
      freshStyles();
      check(
        `panel for ${insider} stays visible under ${periodId}`,
        panels.filter(isVisible).map((el) => el.id.replace(/^detail-/, "")),
        [insider]
      );
    }
    if (outsider) {
      closeAllPanels();
      window.selectPeriod(EXPECT.default_period);
      window.showSession(outsider);
      window.selectPeriod(periodId);
      freshStyles();
      check(
        `panel for ${outsider} disappears under ${periodId}`,
        panels.filter(isVisible).map((el) => el.id.replace(/^detail-/, "")),
        []
      );
    }
  }
  closeAllPanels();
  endSection("detail panels", started);
}

// Expand/collapse must still work exactly as before, independently of the
// period filter: collapsing a group hides its rows even under the default
// period, and expanding brings back exactly the sessions that belong.
{
  const started = section(`[regression] collapse still hides rows under '${EXPECT.default_period}'`);
  window.selectPeriod(EXPECT.default_period);
  expandAllGroups();
  freshStyles();
  const expanded = rows.filter(isVisible).map((el) => el.id.replace(/^row-/, "")).sort();
  collapseAllGroups();
  freshStyles();
  const collapsed = rows.filter(isVisible).map((el) => el.id.replace(/^row-/, ""));
  check("rows visible after expanding all", expanded, EXPECT.periods[EXPECT.default_period].session_ids);
  check("rows visible after collapsing all", collapsed, []);
  endSection("collapse regression", started);
}

// As far as jsdom can go on the sticky overlap: it does no layout AND does
// not resolve var(), so it cannot prove the two sticky elements no longer
// overlap. What it CAN distinguish is whether the trend chart's sticky
// wrapper pins to the same top: 0 as the filter bar (the defect) or offsets
// itself by the bar's height (the fix). The visual result stays unverified -
// that needs a real layout engine.
//
// The sticky rule lives on `body > .period-block` (2026-08-13 fix), not on
// .trend-container itself - .trend-container's own sticky containing block
// used to be that wrapper, which is exactly the container's height, so it
// had zero room to stick regardless of what `top` said. Checking
// .trend-container's computed `top` post-fix would read "auto" (the rule
// moved) and report a false failure on a build that is actually fixed - so
// this checks the wrapper the CSS now targets.
{
  const started = section("[sticky] period filter vs trend container's sticky wrapper");
  const topFilter = window.getComputedStyle(document.querySelector(".period-filter")).top;
  const trendWrapper = document.querySelector("body > .period-block");
  const topTrend = window.getComputedStyle(trendWrapper).top;
  check("period filter pins to viewport top", topFilter, "0px");
  check("trend wrapper is offset, not also at top: 0", topTrend !== "0px" && topTrend !== "", true);
  check(
    "trend wrapper offset uses the filter-bar height variable",
    topTrend.indexOf("--period-filter-height") !== -1,
    true
  );
  endSection("sticky offsets", started);
}

console.log(`\nTOTAL: ${assertionCount} assertions across ${PERIOD_IDS.length} periods`);
console.log(
  failures.length
    ? `FAILED (${failures.length} of ${assertionCount})\n  - ` + failures.join("\n  - ")
    : "ALL ASSERTIONS PASSED"
);
process.exit(failures.length ? 1 : 0);
