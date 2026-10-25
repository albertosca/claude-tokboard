// Drives the REAL generated dashboard.html in jsdom and reports what the
// "By branch" tab of the trend chart does, as JSON on stdout. The Python side
// (tests/test_dashboard_branch_chart_behaviour.py) owns every expectation.
const fileSystem = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
const PERIOD = process.env.PERIOD;
if (!HTML || !PERIOD) {
  console.error("DASHBOARD and PERIOD env vars are required");
  process.exit(2);
}

const dom = new JSDOM(fileSystem.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

const activePanes = () =>
  Array.from(document.querySelectorAll(`#trend-pane-pane-${PERIOD}, #trend-pane-branch-${PERIOD}`))
    .filter((pane) => pane.classList.contains("active"))
    .map((pane) => pane.id);
const selectedTabs = () =>
  Array.from(document.querySelectorAll(`.trend-mode-tabs [aria-selected="true"]`))
    .map((tab) => tab.id)
    .filter((id) => id.endsWith(PERIOD));
const report = {};

report.initial = { panes: activePanes(), tabs: selectedTabs() };

window.highlightProject("group-color-1", { shiftKey: false });
report.projectHighlight = document.body.getAttribute("data-highlight");

window.showTrendMode("branch", PERIOD);
report.afterSwitch = {
  panes: activePanes(),
  tabs: selectedTabs(),
  highlight: document.body.getAttribute("data-highlight"),
};

const chip = document.querySelector(`#trend-pane-branch-${PERIOD} .legend-chip`);
chip.click();
report.chip = {
  highlight: document.body.getAttribute("data-highlight"),
  pressed: chip.getAttribute("aria-pressed"),
};

console.log(JSON.stringify(report));
