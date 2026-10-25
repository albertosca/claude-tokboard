// Drives the REAL generated dashboard.html in jsdom and reports what the
// "other branches" chip of the branch filter does, as JSON on stdout. The
// Python side (tests/test_dashboard_branch_filter_behaviour.py) owns every
// expectation; this file only interrogates the page.
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

const bar = document.querySelector(".branch-filter");
const groupId = bar.getAttribute("data-group");
const restChip = bar.querySelector(".branch-chip[data-branches]");
const rows = () => Array.from(document.querySelectorAll(`.session-row[data-group="${groupId}"]`));
const idOf = (row) => row.id.slice(4);
const shown = () =>
  rows()
    .filter((row) => !row.classList.contains("collapsed") && !row.classList.contains("branch-hidden"))
    .map(idOf);
const pressed = () =>
  Array.from(bar.querySelectorAll(".branch-chip"))
    .filter((chip) => chip.getAttribute("aria-pressed") === "true")
    .map((chip) => chip.textContent);

window.toggleGroup(parseInt(groupId, 10));
const report = { chips: bar.querySelectorAll(".branch-chip").length };
restChip.click();
report.rest = { shown: shown(), pressed: pressed() };
bar.querySelector(".branch-chip[data-branch='']").click();
report.all = { shown: shown() };
console.log(JSON.stringify(report));
