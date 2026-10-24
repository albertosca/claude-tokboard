// Drives the REAL generated dashboard.html in jsdom and reports how detail
// panels are built on demand, as JSON on stdout. The Python side
// (tests/test_dashboard_detail_panels.py) owns every expectation; this file
// only interrogates the page. 2026-10-24: panels are no longer in the page
// until their session is opened.
const fileSystem = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
if (!HTML) {
  console.error("DASHBOARD env var is required (path to the generated dashboard.html)");
  process.exit(2);
}
const PLAIN = process.env.PLAIN_ID;
const OTHER_PLAIN = process.env.OTHER_PLAIN_ID;
const RICH = process.env.RICH_ID;
const OUTSIDE_PERIOD = process.env.OUTSIDE_PERIOD;
const INSIDE_PERIOD = process.env.INSIDE_PERIOD;

const dom = new JSDOM(fileSystem.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

const count = (id) => document.querySelectorAll(`[id="${id}"]`).length;
const openPanels = () => document.querySelectorAll(".detail-panel.open").length;
const panelOf = (id) => document.getElementById(`detail-${id}`);
const report = {};

report.before = { plain: count(`detail-${PLAIN}`), rich: count(`detail-${RICH}`) };

window.showSession(PLAIN);
const plain = panelOf(PLAIN);
report.plain = {
  count: count(`detail-${PLAIN}`),
  open: plain.classList.contains("open"),
  heading: plain.querySelector("h2").textContent,
  controls: document.getElementById(`row-${PLAIN}`).getAttribute("aria-controls"),
  periods: plain.dataset.periods,
  rowPeriods: document.getElementById(`row-${PLAIN}`).dataset.periods,
  emptyLine: plain.querySelector(".empty").textContent,
};
plain.querySelector(".detail-close").click();
report.plain.openAfterCloseButton = plain.classList.contains("open");
window.showSession(PLAIN);
report.plain.countAfterReopen = count(`detail-${PLAIN}`);
report.plain.sameElementAfterReopen = panelOf(PLAIN) === plain;

window.showSession(RICH);
const rich = panelOf(RICH);
report.rich = {
  count: count(`detail-${RICH}`),
  open: rich.classList.contains("open"),
  subagentRows: rich.querySelectorAll(".subagent-row:not(.subagent-header)").length,
  text: rich.textContent,
  controls: document.getElementById(`row-${RICH}`).getAttribute("aria-controls"),
  scriptsInside: rich.querySelectorAll("script").length,
  openPanelsAfterSwitch: openPanels(),
};

window.showSession(PLAIN);
window.selectPeriod(OUTSIDE_PERIOD);
report.period = { hiddenOutside: panelOf(PLAIN).classList.contains("period-hidden") };
window.selectPeriod(INSIDE_PERIOD);
report.period.hiddenBack = panelOf(PLAIN).classList.contains("period-hidden");
report.period.stillOpen = panelOf(PLAIN).classList.contains("open");

window.selectPeriod(OUTSIDE_PERIOD);
window.showSession(OTHER_PLAIN);
report.period.bornHidden = panelOf(OTHER_PLAIN).classList.contains("period-hidden");
window.selectPeriod(INSIDE_PERIOD);

report.emptyId = { threw: false };
try {
  window.showSession("");
  report.emptyId.count = count("detail-");
  report.emptyId.open = Boolean(document.getElementById("detail-")) &&
    document.getElementById("detail-").classList.contains("open");
} catch (error) {
  report.emptyId.threw = true;
}

const panelsBeforeUnknown = document.querySelectorAll(".detail-panel").length;
window.showSession("no-such-session");
report.unknownSession = {
  panelsBefore: panelsBeforeUnknown,
  panelsAfter: document.querySelectorAll(".detail-panel").length,
  open: openPanels(),
};

console.log(JSON.stringify(report));
