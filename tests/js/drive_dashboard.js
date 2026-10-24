// Drives the REAL generated dashboard.html in jsdom: executes its own
// <script>, calls its own toggleGroup / selectPeriod / showSession, and asks
// getComputedStyle what is actually visible. Every assertion is about
// BEHAVIOUR - "the markup contains data-periods" passes whether the filter
// works or not, which is exactly how the inert .period-hidden rule shipped.
//
// Expectations arrive on argv[2] as {periods: {<id>: {session_ids,
// token_total, legend, trend_projects, panorama_projects}}, default_period,
// session_tokens}, computed by the Python side from metrics_core over the
// fixture's own records. Nothing in this file knows what the right answer
// is; it only knows how to interrogate the page.
const fileSystem = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
if (!HTML) {
  console.error("DASHBOARD env var is required (path to the generated dashboard.html)");
  process.exit(2);
}
const EXPECT = JSON.parse(fileSystem.readFileSync(process.argv[2], "utf8"));
const PERIOD_IDS = Object.keys(EXPECT.periods);

const dom = new JSDOM(fileSystem.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

let styleCache = new Map();
function displayOf(element) {
  if (styleCache.has(element)) return styleCache.get(element);
  const display = window.getComputedStyle(element).display;
  styleCache.set(element, display);
  return display;
}
// Visible = neither this element nor any ancestor computes to display:none.
// Ancestors matter for the composition legend, which is hidden by its
// wrapping .period-block rather than by a class of its own.
function isVisible(element) {
  let node = element;
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
// Panels are built on first open (2026-10-24), so the set is read live.
const livePanels = () => Array.from(document.querySelectorAll(".detail-panel[data-periods]"));
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

// The raw token total each header ought to be printing, summed from the
// per-session totals metrics_core supplied. Compared against the header's
// PRINTED figure parsed back to a number, so the check is about the number
// on screen rather than about the presence of the word "tokens".
function groupTokens(sessionIds) {
  const totals = {};
  sessionIds.forEach((id) => {
    const group = groupOfSession.get(id);
    if (group === undefined) return;
    totals[group] = (totals[group] || 0) + EXPECT.session_tokens[id];
  });
  return totals;
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

// Adds up every value the trend chart's own bar tooltips print for a
// period ("<project> · <bucket> - <formatted> tokens"), so the chart is
// asked for a NUMBER and not only for a set of project names. A name set
// cannot tell a chart scoped to the right window from one scoped to a
// wrong window that happens to contain the same projects; the token volume
// can. Returns null when a title fails to parse, so an unreadable chart
// reads as a failure rather than as a zero that might coincidentally match.
function trendTokensDrawn(periodId) {
  const pane = document.getElementById("chart-pane-" + periodId);
  if (!pane) return { value: 0, tolerance: 0, segments: 0 };
  const titles = Array.from(pane.querySelectorAll(".chart-bar title"));
  let value = 0;
  let tolerance = 0;
  for (const title of titles) {
    // Not anchored to end-of-string ($): the trend chart's last bucket,
    // while still in progress, appends " (parcial, NN%)" after "tokens"
    // (2026-09-14, partial-bucket feature) - the value this line extracts
    // is unaffected either way, so the anchor was only ever incidental.
    const match = / - (\S+) tokens\b/.exec(title.textContent.trim());
    const parsed = match && parseFormatted(match[1]);
    if (!parsed) return null;
    value += parsed.value;
    tolerance += parsed.tolerance;
  }
  return { value, tolerance, segments: titles.length };
}

// A period's own trend sub-tabs ("por projeto" / "por tipo de token") live
// inside that period's .trend-container, one such container per
// .period-block[data-period]. Scoped here the same way, so this can tell
// "no pane active in THIS period" from "no pane active anywhere".
function visibleTrendPanes(periodId) {
  const container = document.querySelector(
    '.period-block[data-period="' + periodId + '"] .trend-container'
  );
  if (!container) return [];
  return Array.from(container.querySelectorAll(".trend-pane")).filter(isVisible);
}

// Panorama's rows are <text> label, then the bar, then a <text> carrying
// the printed grand total - so the visible labels and the visible totals
// pair up positionally, in document order. The total's text is
// "<formatted>[ · há N dias]"; only the leading figure is the number.
function panoramaTotalsDrawn() {
  const labels = Array.from(document.querySelectorAll(".project-chart-label")).filter(isVisible);
  const totals = Array.from(document.querySelectorAll(".total-grand")).filter(isVisible);
  if (labels.length !== totals.length) return null;
  const drawn = {};
  labels.forEach((label, index) => {
    const name = label.textContent.replace(/\s*●$/, "").trim();
    drawn[name] = parseFormatted(totals[index].textContent.split("·")[0]);
  });
  return drawn;
}

// Which projects print a total that is not what their own in-period
// sessions add up to - same shape, and same rounding tolerance, as the
// group headers' token check.
function panoramaTotalMismatches(want, drawn) {
  if (drawn === null) return ["Panorama's labels and printed totals do not pair up"];
  const mismatches = [];
  Object.keys(want).forEach((project) => {
    const shown = drawn[project];
    if (!shown || Math.abs(shown.value - want[project]) > shown.tolerance) {
      mismatches.push(
        `${project} prints ${shown ? shown.value : "nothing"}, expected ${want[project]} tokens`
      );
    }
  });
  return mismatches.sort();
}

function toggleTo(header, wantCollapsed) {
  if (header.classList.contains("collapsed") !== wantCollapsed) {
    window.toggleGroup(parseInt(header.id.replace("group-", ""), 10));
  }
}
function expandAllGroups() {
  headers.forEach((header) => toggleTo(header, false));
}
function collapseAllGroups() {
  headers.forEach((header) => toggleTo(header, true));
}
function resetScroll() {
  allTrendScrolls.forEach((element) => { element.scrollLeft = 0; });
}
function closeAllPanels() {
  livePanels().forEach((panel) => {
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
    visibleLegends[0].querySelectorAll(".legend-total").forEach((element) => {
      legend[element.getAttribute("data-series")] = element.textContent.trim();
    });
  }
  // A header is only allowed on screen if at least one of its own sessions
  // belongs to the period, and BOTH numbers it prints have to describe what
  // the reader can actually see. Critical #2's literal wording was about the
  // token figure, not the count - every group header reading the all-time
  // token total under every period, ontem included - so a regex that captured only
  // "(N sess" would have watched that exact defect go straight past.
  const headerCounts = {};
  const headerTokenText = {};
  let headersWithoutCount = 0;
  visibleHeaders.forEach((header) => {
    const span = Array.from(header.querySelectorAll(".group-count")).find(isVisible);
    const parsed = span && /^\((\d+) sess\S* · (.+) tokens\)$/.exec(span.textContent.trim());
    if (!parsed) {
      headersWithoutCount += 1;
      return;
    }
    const group = header.id.replace("group-", "");
    headerCounts[group] = parseInt(parsed[1], 10);
    headerTokenText[group] = parsed[2].trim();
  });
  const activeScrolls = Array.from(
    document.querySelectorAll('.period-block[data-period="' + periodId + '"] .trend-scroll')
  );
  return {
    sessionIds: visibleRows.map((element) => element.id.replace(/^row-/, "")).sort(),
    rowsNotFlex: visibleRows
      .filter((element) => displayOf(element) !== "flex")
      .map((element) => element.id + "=" + displayOf(element))
      .sort(),
    headerGroups: visibleHeaders.map((header) => header.id.replace("group-", "")).sort(),
    headerCounts,
    headerTokenText,
    headersWithoutCount,
    legendBlocks: visibleLegends.length,
    legend,
    visiblePanelIds: livePanels().filter(isVisible).map((element) => element.id.replace(/^detail-/, "")).sort(),
    trendScrolls: activeScrolls.length,
    trendScrollsAtNewest: activeScrolls.filter((element) => element.scrollLeft === SCROLL_WIDTH).length,
  };
}

const failures = [];
let assertionCount = 0;
function check(label, actual, expected) {
  assertionCount += 1;
  const matches = JSON.stringify(actual) === JSON.stringify(expected);
  if (!matches) {
    failures.push(
      `${label}\n      got:      ${JSON.stringify(actual)}\n      expected: ${JSON.stringify(expected)}`
    );
  }
  console.log(`  ${matches ? "PASS" : "FAIL"} ${label}: ${JSON.stringify(actual)}`);
}

function section(title) {
  console.log(`\n${title}`);
  return assertionCount;
}
function endSection(title, startedAt) {
  console.log(`  -> ${title}: ${assertionCount - startedAt} assertions`);
}

// Which visible headers print a token figure that is not what their own
// in-period sessions add up to. Parsed back to a number and compared within
// format_tokens' own rounding, exactly like the composition legend's sum.
function headerTokenMismatches(sessionIds, got) {
  const wantTokens = groupTokens(sessionIds);
  const mismatches = [];
  Object.keys(wantTokens).forEach((group) => {
    const shown = parseFormatted(got.headerTokenText[group]);
    if (!shown || Math.abs(shown.value - wantTokens[group]) > shown.tolerance) {
      mismatches.push(
        `group ${group} prints ${JSON.stringify(got.headerTokenText[group])}, ` +
        `expected ${wantTokens[group]} tokens`
      );
    }
  });
  return mismatches.sort();
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
  check("token figure printed by each visible header", headerTokenMismatches(want.session_ids, got), []);
  check("visible headers with no visible count", got.headersWithoutCount, 0);
  // Per period, not just on load: freezing this loop leaves the highlight
  // stuck on the default while the rows below it change underneath, so the
  // reader is told they are looking at a period they are not.
  check(
    "period chips marked active",
    Array.from(document.querySelectorAll(".period-chip.active")).map((chip) => chip.dataset.period),
    [periodId]
  );
  check("visible composition legends", got.legendBlocks, 1);
  check("composition legend chips", got.legend, want.legend);
  const chips = Object.keys(want.legend).map((series) => parseFormatted(got.legend[series]));
  check("composition legend chips all parse as numbers", chips.every(Boolean), true);
  if (chips.every(Boolean)) {
    const sum = chips.reduce((total, chip) => total + chip.value, 0);
    const tolerance = chips.reduce((total, chip) => total + chip.tolerance, 0);
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
  check("token figure printed by each header on load", headerTokenMismatches(want.session_ids, initial), []);
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

// The composition legend, the trend chart and the Panorama chart are all
// pre-rendered once per period from the same filter_sessions_by_window call.
// Asserting only the legend's CONTENT leaves its two siblings free to render
// all-time data under every period undetected - a defect found is a class,
// not an instance. Neither of these needs SVG geometry: the set of project
// names each chart is built from is data-derived and already enough to tell
// a scoped chart from an unscoped one.
{
  const started = section("[charts] trend and Panorama are scoped to the period too");
  for (const periodId of PERIOD_IDS) {
    const want = EXPECT.periods[periodId];
    window.selectPeriod(periodId);
    freshStyles();
    // The trend chart lives outside the tab panes (it is the sticky
    // body > .period-block), so its "por projeto" legend is on screen with
    // no tab switch. The inactive "por tipo de token" pane's empty slot is
    // filtered out by isVisible rather than by a selector.
    const trendProjects = Array.from(document.querySelectorAll(".trend-legend-slot .legend-chip"))
      .filter(isVisible)
      .map((element) => element.textContent.trim())
      .sort();
    check(`trend chart legend names at least one project under ${periodId}`, trendProjects.length > 0, true);
    check(`trend chart is built from ${periodId}'s data alone`, trendProjects, want.trend_projects);

    // The names say WHICH projects; this says HOW MUCH. Guarded by a
    // non-zero segment count, because a chart that rendered nothing at all
    // would otherwise sum to 0 and could match a period that is empty.
    const drawnTokens = trendTokensDrawn(periodId);
    check(`trend chart under ${periodId} drew bar segments at all`, drawnTokens !== null && drawnTokens.segments > 0, true);
    if (drawnTokens !== null) {
      check(
        `trend chart bars under ${periodId} add up to the period's tokens ` +
        `(${want.trend_tokens} +/- ${drawnTokens.tolerance})`,
        Math.abs(drawnTokens.value - want.trend_tokens) <= drawnTokens.tolerance,
        true
      );
    }

    // Panorama sits inside its own tab pane, so it is display:none until the
    // page's own showTab makes it active. Switching for real keeps every
    // reading a getComputedStyle reading instead of an attribute inspection.
    window.showTab("panorama");
    freshStyles();
    const panoramaProjects = Array.from(document.querySelectorAll(".project-chart-label"))
      .filter(isVisible)
      // " ●" marks a project open right now - not part of its name.
      .map((element) => element.textContent.replace(/\s*●$/, "").trim())
      .sort();
    check(`Panorama chart names at least one project under ${periodId}`, panoramaProjects.length > 0, true);
    check(`Panorama chart is built from ${periodId}'s data alone`, panoramaProjects, want.panorama_projects);
    check(
      `token figure printed by each Panorama row under ${periodId}`,
      panoramaTotalMismatches(want.panorama_totals, panoramaTotalsDrawn()),
      []
    );
    window.showTab("sessions");
  }
  endSection("chart scoping", started);
}

// A trend sub-tab click (showTrendMode) must only touch the period it was
// clicked in. It used to clear 'active' from every .trend-pane and every
// .trend-mode-tabs button DOCUMENT-WIDE, only re-adding it for the period
// actually operated on - so every OTHER period was left with no active pane
// at all, and switching there next showed no chart whatsoever (shipped bug,
// 2026-08-18: "o gráfico sumiu pra todas exceto o filtro que eu tava, de
// ontem" - reproduced by clicking either trend sub-tab and then changing the
// period; changing the period alone, or clicking the sub-tab alone, was
// fine on its own - only the combination broke it).
{
  const started = section("[trend-mode] a sub-tab click on one period leaves every other period's chart intact");
  for (const clickedPeriod of PERIOD_IDS) {
    for (const mode of ["pane", "tokens", "model"]) {
      window.selectPeriod(clickedPeriod);
      window.showTrendMode(mode, clickedPeriod);
      freshStyles();
      const clicked = visibleTrendPanes(clickedPeriod);
      check(
        `clicked period ${clickedPeriod} shows exactly its '${mode}' pane after showTrendMode`,
        clicked.map((element) => element.id),
        ["trend-pane-" + mode + "-" + clickedPeriod]
      );
      for (const otherPeriod of PERIOD_IDS) {
        if (otherPeriod === clickedPeriod) continue;
        window.selectPeriod(otherPeriod);
        freshStyles();
        const stillVisible = visibleTrendPanes(otherPeriod);
        check(
          `switching to ${otherPeriod} after showTrendMode('${mode}', '${clickedPeriod}') still shows one chart`,
          stillVisible.length,
          1
        );
      }
    }
  }
  endSection("trend-mode isolation", started);
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
        livePanels().filter(isVisible).map((element) => element.id.replace(/^detail-/, "")),
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
        livePanels().filter(isVisible).map((element) => element.id.replace(/^detail-/, "")),
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
  const expanded = rows.filter(isVisible).map((element) => element.id.replace(/^row-/, "")).sort();
  collapseAllGroups();
  freshStyles();
  const collapsed = rows.filter(isVisible).map((element) => element.id.replace(/^row-/, ""));
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
  // This one assertion's green depends on a jsdom LIMITATION, not on a
  // guarantee: jsdom does not resolve custom properties, so
  // getComputedStyle().top hands back the literal "var(--period-filter-height)"
  // instead of a pixel value. A jsdom release that started resolving var()
  // would make this fail on a build that is perfectly correct - a false RED.
  // That is why package-lock.json is committed (package.json only asks for
  // a caret range): the pin is what keeps this readable. Re-measured against
  // jsdom 30.1.0 on 2026-09-21 and still literal. If a future refresh starts
  // resolving var(), rewrite this check to accept a resolved pixel value too,
  // rather than "fix" the CSS it is pointing at.
  check(
    "trend wrapper offset uses the filter-bar height variable",
    topTrend.indexOf("--period-filter-height") !== -1,
    true
  );
  endSection("sticky offsets", started);
}

// "Por modelo" shipped with inert bands: the rects and the legend chips
// carried a colour and nothing else, so clicking either did nothing at all
// (reported 2026-09-21, "por modelo nao ta funcionando o clicar no grafico
// nem na legenda"). Markup assertions could not have caught it - a rect with
// a fill IS valid markup. What catches it is driving the click and asking
// getComputedStyle whether the other bands actually dimmed.
{
  const started = section("[model] clicking a band or its chip spotlights that model");
  const period = EXPECT.default_period;
  window.selectPeriod(period);
  window.showTrendMode("model", period);
  freshStyles();

  const pane = document.getElementById("trend-pane-model-" + period);
  const bands = Array.from(pane.querySelectorAll("svg .chart-bar"));
  const chips = Array.from(pane.querySelectorAll(".legend-chip"));
  check("the model pane draws more than one band", bands.length > 1, true);
  check("the model pane has a chip per band colour", chips.length > 1, true);

  // A band and a chip that disagree on the class would each light the
  // other's colour - the failure this pairing exists to refuse.
  const bandClasses = new Set(bands.map((band) => band.getAttribute("class").replace("chart-bar ", "")));
  const chipClasses = new Set(
    chips.map((chip) => chip.querySelector(".legend-swatch").getAttribute("class").replace("legend-swatch ", ""))
  );
  check(
    "every band colour has a chip of the same colour",
    Array.from(bandClasses).every((bandClass) => chipClasses.has(bandClass)),
    true
  );

  // A NAMED model, not the "sem modelo" remainder: the remainder would
  // prove the mechanism while sidestepping the thing that was reported.
  const named = bands.find((band) => band.getAttribute("class").indexOf("group-color-none") === -1);
  check("the pane draws a band for a named model", Boolean(named), true);
  const target = named.getAttribute("class").replace("chart-bar ", "");
  const other = bands.find((band) => band.getAttribute("class").indexOf(target) === -1);
  check("the pane has a second, differently-coloured band to dim", Boolean(other), true);

  named.onclick(new window.MouseEvent("click"));
  freshStyles();
  check(
    "clicking a band records the selection on <body>",
    document.body.getAttribute("data-highlight"),
    target
  );
  check(
    "the clicked band stays at full opacity",
    window.getComputedStyle(named).opacity,
    "1"
  );
  check(
    "a band of another model dims",
    window.getComputedStyle(other).opacity,
    "0.25"
  );

  // Clicking the same thing again clears it, and the chip drives the same
  // selection as the band it belongs to.
  named.onclick(new window.MouseEvent("click"));
  check("clicking the same band again clears the selection", document.body.hasAttribute("data-highlight"), false);

  const chipForTarget = chips.find(
    (chip) => chip.querySelector(".legend-swatch").getAttribute("class").indexOf(target) !== -1
  );
  chipForTarget.onclick(new window.MouseEvent("click"));
  check(
    "clicking the chip selects the same class its band does",
    document.body.getAttribute("data-highlight"),
    target
  );

  // The three modes share one data-highlight vocabulary, so a selection
  // must not survive a mode switch: group-color-3 means a model here and a
  // project one tab over.
  window.showTrendMode("pane", period);
  check(
    "switching mode clears a selection that means something else there",
    document.body.hasAttribute("data-highlight"),
    false
  );
  endSection("model highlighting", started);
}

console.log(`\nTOTAL: ${assertionCount} assertions across ${PERIOD_IDS.length} periods`);
console.log(
  failures.length
    ? `FAILED (${failures.length} of ${assertionCount})\n  - ` + failures.join("\n  - ")
    : "ALL ASSERTIONS PASSED"
);
process.exit(failures.length ? 1 : 0);
