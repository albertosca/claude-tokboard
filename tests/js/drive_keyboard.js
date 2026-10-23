// Drives the REAL generated dashboard.html in jsdom with the keyboard and
// reports what changed, as JSON on stdout. The Python side
// (tests/test_dashboard_keyboard.py) owns every expectation; this file only
// interrogates the page. 2026-10-05 review, items 2 and 9: the dashboard's
// controls were spans and divs with an onclick and nothing else - no role,
// no tabindex, no state - so a keyboard user reached none of them.
const fileSystem = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
if (!HTML) {
  console.error("DASHBOARD env var is required (path to the generated dashboard.html)");
  process.exit(2);
}

const dom = new JSDOM(fileSystem.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

function press(element, key, modifiers = {}) {
  element.focus();
  const event = new window.KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...modifiers,
  });
  element.dispatchEvent(event);
  return event.defaultPrevented;
}

function attributesOf(selector) {
  return Array.from(document.querySelectorAll(selector)).map((element) => ({
    role: element.getAttribute("role"),
    tabindex: element.getAttribute("tabindex"),
  }));
}

const report = {};

report.tabs = attributesOf(".tab-btn");
report.tabControls = Array.from(document.querySelectorAll(".tab-btn")).map((tab) => {
  const controls = tab.getAttribute("aria-controls");
  return { id: tab.id, controls, resolves: Boolean(controls && document.getElementById(controls)) };
});
report.tabPanels = Array.from(document.querySelectorAll(".tab-btn")).map((tab) => {
  const panel = document.getElementById(tab.getAttribute("aria-controls"));
  return {
    id: tab.id,
    role: panel && panel.getAttribute("role"),
    labelledby: panel && panel.getAttribute("aria-labelledby"),
  };
});
report.tablists = Array.from(document.querySelectorAll(".tab-bar, .trend-mode-tabs")).map(
  (element) => element.getAttribute("role"),
);
report.legendChips = attributesOf(".legend-chip[onclick]");
report.groupHeaders = attributesOf(".group-header");
report.sessionRows = attributesOf(".session-row:not(.session-header)");
report.h1 = (document.querySelector("h1") || { textContent: null }).textContent;
report.hasMain = Boolean(document.querySelector("main"));
report.charts = Array.from(document.querySelectorAll("svg.trend-svg")).map((svg) => ({
  role: svg.getAttribute("role"),
  label: svg.getAttribute("aria-label"),
}));

// A group header: Enter expands it and says so.
const header = document.querySelector(".group-header");
const groupId = header.id.replace("group-", "");
report.headerExpandedBefore = header.getAttribute("aria-expanded");
report.enterOnHeaderPrevented = press(header, "Enter");
report.headerExpandedAfter = header.getAttribute("aria-expanded");
report.repeatOnHeaderPrevented = press(header, "Enter", { repeat: true });
report.headerExpandedAfterRepeat = header.getAttribute("aria-expanded");
report.rowsCollapsedAfterEnter = Array.from(
  document.querySelectorAll(`.session-row[data-group="${groupId}"]`),
).filter((row) => row.classList.contains("collapsed")).length;

// The empty detail pane explains itself until a session is open
// (2026-10-05 review, item 13).
function hintDisplay() {
  const hint = document.querySelector(".detail-container .detail-hint");
  return hint ? window.getComputedStyle(hint).display : null;
}
report.hintText = (document.querySelector(".detail-hint") || { textContent: null }).textContent;
report.hintDisplayBefore = hintDisplay();

// A session row: Space opens its panel and does not scroll the page.
const row = document.querySelector(`.session-row[data-group="${groupId}"]`);
const sessionId = row.id.replace("row-", "");
report.spaceOnRowPrevented = press(row, " ");
report.panelOpenAfterSpace = document
  .getElementById(`detail-${sessionId}`)
  .classList.contains("open");
report.rowControls = row.getAttribute("aria-controls");
report.hintDisplayAfterOpen = hintDisplay();
window.closeDetail(sessionId);
report.hintDisplayAfterClose = hintDisplay();
window.showSession(sessionId);

// A key that is not an activation key does nothing.
const otherRow = document.querySelectorAll(`.session-row[data-group="${groupId}"]`)[1] || row;
report.letterOnRowPrevented = press(otherRow, "a");

// Period chips: exactly one pressed, and the live region says which.
const chips = Array.from(document.querySelectorAll(".period-chip"));
// The first chip, not the page's default: the call must move the state.
const target = chips[0];
window.selectPeriod(target.dataset.period);
report.pressedChips = chips
  .filter((chip) => chip.getAttribute("aria-pressed") === "true")
  .map((chip) => chip.dataset.period);
report.selectedPeriod = target.dataset.period;
report.selectedPeriodName = target.textContent.trim();
const live = document.querySelector("[aria-live]");
report.liveText = live ? live.textContent : null;

// A hidden row cannot be reached by Tab. jsdom does no layout, so the
// focus order itself cannot be run here; what a browser bases it on can:
// every row hidden by collapse or by the period filter computes to
// display:none (a display:none element is skipped by Tab and refuses
// focus()). Measured in a real browser on 2026-10-05.
function displayOf(selector) {
  const elements = Array.from(document.querySelectorAll(selector));
  return {
    count: elements.length,
    displays: Array.from(new Set(elements.map((element) => window.getComputedStyle(element).display))),
  };
}
report.hiddenCollapsedRows = displayOf(".session-row.collapsed");
report.hiddenPeriodRows = displayOf(".session-row.period-hidden");
report.hiddenPeriodHeaders = displayOf(".group-header.period-hidden");

// Legend chips (the by-project pane, open by default): pressing one marks
// exactly that one pressed.
const container = document.querySelector(".period-block:not([hidden]) .trend-container");
const chip = container.querySelector(".trend-pane.active .legend-chip[onclick]");
press(chip, "Enter");
report.chipPressed = chip.getAttribute("aria-pressed");
report.otherChipsPressed = Array.from(
  container.querySelectorAll(".trend-pane.active .legend-chip[onclick]"),
)
  .filter((element) => element !== chip)
  .map((element) => element.getAttribute("aria-pressed"));

// Shift+Enter on a second chip ADDS it to the selection (what shift+click
// does), instead of replacing it.
const otherChip = container.querySelector(".trend-pane.active .legend-chip[onclick]:not([aria-pressed='true'])");
press(otherChip, "Enter", { shiftKey: true });
report.pressedChipsAfterShift = Array.from(
  container.querySelectorAll(".trend-pane.active .legend-chip[onclick]"),
).filter((element) => element.getAttribute("aria-pressed") === "true").length;

// Chart-mode tabs: the selected one says so, through the keyboard too.
const modeTabs = Array.from(container.querySelectorAll(".trend-mode-tabs .tab-btn"));
press(modeTabs[1], "Enter");
report.modeTabsSelected = modeTabs.map((tab) => tab.getAttribute("aria-selected"));
report.modeTabsTabindex = modeTabs.map((tab) => tab.getAttribute("tabindex"));

// Arrow keys walk the tabs of one tablist, wrapping, and select as they go.
function selectedIndex(tabs) {
  return tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true");
}
report.arrowTrail = [];
for (const key of ["ArrowRight", "ArrowRight", "ArrowLeft", "Home", "End"]) {
  const current = modeTabs[selectedIndex(modeTabs)];
  const prevented = press(current, key);
  report.arrowTrail.push({
    key,
    prevented,
    selected: selectedIndex(modeTabs),
    focused: modeTabs.indexOf(document.activeElement),
  });
}

// The page tabs (Sessions / Overview).
const pageTabs = Array.from(document.querySelectorAll(".tab-bar .tab-btn"));
press(pageTabs[1], "Enter");
report.pageTabsSelected = pageTabs.map((tab) => tab.getAttribute("aria-selected"));
report.pageTabsTabindex = pageTabs.map((tab) => tab.getAttribute("tabindex"));

process.stdout.write(JSON.stringify(report));
