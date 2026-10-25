// Drives the REAL generated dashboard.html in jsdom and reports what the
// branch filter of the session list does, as JSON on stdout. The Python side
// (tests/test_dashboard_branch_filter_behaviour.py) owns every expectation;
// this file only interrogates the page.
const fileSystem = require("fs");
const { JSDOM } = require("jsdom");

const HTML = process.env.DASHBOARD;
if (!HTML) {
  console.error("DASHBOARD env var is required (path to the generated dashboard.html)");
  process.exit(2);
}
const OUTSIDE_PERIOD = process.env.OUTSIDE_PERIOD;
const INSIDE_PERIOD = process.env.INSIDE_PERIOD;

const dom = new JSDOM(fileSystem.readFileSync(HTML, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

const bar = document.querySelector(".branch-filter");
const groupId = bar.getAttribute("data-group");
const chipFor = (branch) =>
  Array.from(bar.querySelectorAll(".branch-chip")).find(
    (chip) => chip.getAttribute("data-branch") === branch,
  );
const rows = () => Array.from(document.querySelectorAll(`.session-row[data-group="${groupId}"]`));
const idOf = (row) => row.id.slice(4);
const shown = () =>
  rows()
    .filter((row) => !row.classList.contains("collapsed") && !row.classList.contains("branch-hidden"))
    .map(idOf);
const hiddenByFilter = () => rows().filter((row) => row.classList.contains("branch-hidden")).map(idOf);
const pressed = () =>
  Array.from(bar.querySelectorAll(".branch-chip"))
    .filter((chip) => chip.getAttribute("aria-pressed") === "true")
    .map((chip) => chip.getAttribute("data-branch"));
const report = {};

window.toggleGroup(parseInt(groupId, 10));
report.open = { shown: shown(), pressed: pressed() };

chipFor("main").click();
report.main = { shown: shown(), hidden: hiddenByFilter(), pressed: pressed() };

window.selectPeriod(OUTSIDE_PERIOD);
window.selectPeriod(INSIDE_PERIOD);
report.afterPeriodSweep = { hidden: hiddenByFilter(), pressed: pressed() };

chipFor('quote"d').click();
report.quoted = { shown: shown(), pressed: pressed() };

chipFor("main").click();
window.showSession("feature-session");
report.showHidden = {
  hidden: hiddenByFilter(),
  pressed: pressed(),
  active: document.getElementById("row-feature-session").classList.contains("active"),
};

window.showSession("main-session");
chipFor('quote"d').click();
const panel = document.getElementById("detail-main-session");
report.panel = {
  open: panel.classList.contains("open"),
  hiddenWithRow: panel.classList.contains("branch-hidden"),
};
chipFor("").click();
report.panel.hiddenAfterAll = panel.classList.contains("branch-hidden");

report.noBranchChip = {
  present: Boolean(chipFor("-")),
};
chipFor("-").click();
report.noBranchChip.shown = shown();

console.log(JSON.stringify(report));
