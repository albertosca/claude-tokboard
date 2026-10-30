// Clicks the page-level tabs of the REAL generated dashboard.html in jsdom and
// reports which pane and tab ended up active. The Python side
// (tests/test_dashboard_hypotheses_tab.py) owns every expectation.
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

function state() {
  return {
    activePanes: Array.from(document.querySelectorAll(".tab-pane.active")).map((pane) => pane.id),
    selectedTabs: Array.from(document.querySelectorAll('.tab-bar [role="tab"]'))
      .filter((tab) => tab.getAttribute("aria-selected") === "true")
      .map((tab) => tab.id),
    tabOrder: Array.from(document.querySelectorAll('.tab-bar [role="tab"]')).map((tab) => tab.id),
  };
}

const report = { initial: state() };
document.getElementById("tab-btn-hypotheses").click();
report.afterClick = state();

const panorama = document.getElementById("tab-btn-panorama");
panorama.focus();
panorama.dispatchEvent(
  new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
);
report.afterArrowRight = state();

const hypotheses = document.getElementById("tab-btn-hypotheses");
hypotheses.dispatchEvent(
  new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
);
report.afterWrap = state();

const summary = document.querySelector("#tab-hypotheses details > summary");
report.summaryFound = summary !== null;
if (summary) {
  const details = summary.parentElement;
  report.openBefore = details.open;
  summary.click();
  report.openAfter = details.open;
}
process.stdout.write(JSON.stringify(report));
