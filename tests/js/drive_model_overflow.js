// A model past the colour palette (ModelColorSlot.is_overflow, backlog
// sweep 3 / M1) must dim and highlight INDEPENDENTLY of the real slot
// holder whose colour it reuses - clicking either one must never also
// light up the other. Markup alone cannot prove this: the overflow band
// and its donor can carry two perfectly distinct class names in the HTML
// while a CSS rule elsewhere still lights both (or neither) together, which
// is exactly what happened here on first implementation - a stylesheet
// list long enough to include every model-overflow-N selector silently
// stopped matching in jsdom's CSS engine, and only asking
// getComputedStyle caught it (2026-09-29). Assertions use Node's own
// `assert`, so any failure aborts with a nonzero exit and a stack trace.
const assert = require("assert");
const filesystem = require("fs");
const { JSDOM } = require("jsdom");

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error("usage: node drive_model_overflow.js <path-to-fragment.html>");
  process.exit(2);
}

const dom = new JSDOM(filesystem.readFileSync(htmlPath, "utf8"), { runScripts: "dangerously" });
const { window } = dom;
const { document } = window;

const bands = Array.from(document.querySelectorAll("svg .chart-bar"));
const overflowBand = bands.find(
  (band) => band.getAttribute("class").indexOf("model-overflow-") !== -1
);
assert(overflowBand, "no model-overflow-N band found in the rendered chart");
const overflowClass = overflowBand.getAttribute("class").replace("chart-bar ", "");
const donorClass = overflowClass.replace("model-overflow-", "group-color-");
const donorBand = bands.find((band) => band.getAttribute("class") === "chart-bar " + donorClass);
assert(donorBand, `no donor band of class ${donorClass} found`);

const chips = Array.from(document.querySelectorAll(".legend-chip"));
const overflowChip = chips.find(
  (chip) =>
    chip.querySelector(".legend-swatch").getAttribute("class").indexOf(overflowClass) !== -1
);
const donorChip = chips.find(
  (chip) => chip.querySelector(".legend-swatch").getAttribute("class").indexOf(donorClass) !== -1
);
assert(overflowChip, "no legend chip for the overflow model");
assert(donorChip, "no legend chip for the donor model");

function opacityOf(element) {
  return window.getComputedStyle(element).opacity;
}

// Clicking the overflow band must highlight only itself, never its donor.
overflowBand.onclick(new window.MouseEvent("click"));
assert.strictEqual(
  opacityOf(overflowBand),
  "1",
  "the clicked overflow band must stay full opacity"
);
assert.strictEqual(
  opacityOf(donorBand),
  "0.25",
  "the donor band must dim when the overflow model is clicked"
);
assert.strictEqual(
  opacityOf(donorChip),
  "0.4",
  "the donor chip must dim when the overflow model is clicked"
);
assert.strictEqual(
  window.getComputedStyle(overflowChip).fontWeight,
  "700",
  "the overflow model's own chip must bold when its band is clicked"
);

// Clear the selection, then click the donor: the overflow band must dim in
// the other direction just as reliably.
overflowBand.onclick(new window.MouseEvent("click"));
donorBand.onclick(new window.MouseEvent("click"));
assert.strictEqual(opacityOf(donorBand), "1", "the clicked donor band must stay full opacity");
assert.strictEqual(
  opacityOf(overflowBand),
  "0.25",
  "the overflow band must dim when the donor is clicked"
);

console.log("PASS: overflow and donor bands highlight independently");
