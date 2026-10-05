"""Static CSS and JS for dashboard_html.py's rendered page.

Extracted from render_html() (2026-08-26, Phase 2 of the Python quality
standard initiative) - both blocks are pure static content with zero
Python interpolation, confirmed before this file existed. Kept as a
separate module purely to get render_html() down to its actual logic;
these two constants are not meant to be imported anywhere else.
"""

STYLE = """
    <style>
      :root {
        --surface-1: #fcfcfb;
        --text-primary: #0b0b0b;
        --text-secondary: #52514e;
        /* 4.98:1 on --surface-1; the old #898781 was 3.5:1, under WCAG's
           4.5:1 for the 9-11px text it colours (2026-10-05 review). */
        --text-muted: #6f6e69;
        --border: #e1e0d9;
        /* Pinned height for the sticky period filter, so the trend chart's
           own sticky wrapper (body > .period-block, see below) can offset
           itself by exactly the bar's height instead of being covered by it
           on scroll. Defined once and consumed by both rules, so the two
           cannot drift apart. The dark @media block below only overrides
           colours, so this stays in effect in both themes. */
        --period-filter-height: 44px;
        --series-1: #2a78d6;
        --series-2: #1baf7a;
        --series-3: #eda100;
        --series-4: #008300;
        /* Slots 1-8 are the projects' colours (metrics_core.assign_project_slots):
           this order passed the dataviz palette validator in both themes on
           2026-10-05 - no adjacent pair under the normal-vision floor of 15.
           Slots 9-30 remain for the model chart only. --group-other is the
           "others" bucket, kept a clear step away from --group-none's gray. */
        --group-1: #2a78d6; --group-1-tint: rgba(42,120,214,0.08);
        --group-2: #eb6834; --group-2-tint: rgba(235,104,52,0.08);
        --group-3: #1baf7a; --group-3-tint: rgba(27,175,122,0.08);
        --group-4: #4a3aa7; --group-4-tint: rgba(74,58,167,0.08);
        --group-5: #eda100; --group-5-tint: rgba(237,161,0,0.08);
        --group-6: #e87ba4; --group-6-tint: rgba(232,123,164,0.08);
        --group-7: #008300; --group-7-tint: rgba(0,131,0,0.08);
        --group-8: #e34948; --group-8-tint: rgba(227,73,72,0.08);
        --group-9: #6bc62f; --group-9-tint: rgba(107,198,47,0.08);
        --group-10: #4dc62f; --group-10-tint: rgba(77,198,47,0.08);
        --group-11: #2fc62f; --group-11-tint: rgba(47,198,47,0.08);
        --group-12: #2fc64d; --group-12-tint: rgba(47,198,77,0.08);
        --group-13: #2fc66b; --group-13-tint: rgba(47,198,107,0.08);
        --group-14: #2fc68a; --group-14-tint: rgba(47,198,138,0.08);
        --group-15: #2fc6a8; --group-15-tint: rgba(47,198,168,0.08);
        --group-16: #2fc6c6; --group-16-tint: rgba(47,198,198,0.08);
        --group-17: #2fa8c6; --group-17-tint: rgba(47,168,198,0.08);
        --group-18: #2f8ac6; --group-18-tint: rgba(47,138,198,0.08);
        --group-19: #2f6bc6; --group-19-tint: rgba(47,107,198,0.08);
        --group-20: #2f4dc6; --group-20-tint: rgba(47,77,198,0.08);
        --group-21: #2f2fc6; --group-21-tint: rgba(47,47,198,0.08);
        --group-22: #4d2fc6; --group-22-tint: rgba(77,47,198,0.08);
        --group-23: #6b2fc6; --group-23-tint: rgba(107,47,198,0.08);
        --group-24: #8a2fc6; --group-24-tint: rgba(138,47,198,0.08);
        --group-25: #a82fc6; --group-25-tint: rgba(168,47,198,0.08);
        --group-26: #c62fc6; --group-26-tint: rgba(198,47,198,0.08);
        --group-27: #c62fa8; --group-27-tint: rgba(198,47,168,0.08);
        --group-28: #c62f8a; --group-28-tint: rgba(198,47,138,0.08);
        --group-29: #c62f6b; --group-29-tint: rgba(198,47,107,0.08);
        --group-30: #c62f4d; --group-30-tint: rgba(198,47,77,0.08);
        --group-none: #898781; --group-none-tint: transparent;
        --group-other: #4a4945;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --surface-1: #1a1a19;
          --text-primary: #ffffff;
          --text-secondary: #c3c2b7;
          --text-muted: #898781;
          --border: #2c2c2a;
          --series-1: #3987e5;
          --series-2: #199e70;
          --series-3: #c98500;
          --series-4: #008300;
          --group-1: #3987e5; --group-1-tint: rgba(57,135,229,0.12);
          --group-2: #d95926; --group-2-tint: rgba(217,89,38,0.12);
          --group-3: #199e70; --group-3-tint: rgba(25,158,112,0.12);
          --group-4: #9085e9; --group-4-tint: rgba(144,133,233,0.12);
          --group-5: #c98500; --group-5-tint: rgba(201,133,0,0.12);
          --group-6: #d55181; --group-6-tint: rgba(213,81,129,0.12);
          --group-7: #008300; --group-7-tint: rgba(0,131,0,0.12);
          --group-8: #e66767; --group-8-tint: rgba(230,103,103,0.12);
          --group-9: #8cdc56; --group-9-tint: rgba(140,220,86,0.12);
          --group-10: #71dc56; --group-10-tint: rgba(113,220,86,0.12);
          --group-11: #56dc56; --group-11-tint: rgba(86,220,86,0.12);
          --group-12: #56dc71; --group-12-tint: rgba(86,220,113,0.12);
          --group-13: #56dc8c; --group-13-tint: rgba(86,220,140,0.12);
          --group-14: #56dca6; --group-14-tint: rgba(86,220,166,0.12);
          --group-15: #56dcc1; --group-15-tint: rgba(86,220,193,0.12);
          --group-16: #56dcdc; --group-16-tint: rgba(86,220,220,0.12);
          --group-17: #56c1dc; --group-17-tint: rgba(86,193,220,0.12);
          --group-18: #56a6dc; --group-18-tint: rgba(86,166,220,0.12);
          --group-19: #568cdc; --group-19-tint: rgba(86,140,220,0.12);
          --group-20: #5671dc; --group-20-tint: rgba(86,113,220,0.12);
          --group-21: #5656dc; --group-21-tint: rgba(86,86,220,0.12);
          --group-22: #7156dc; --group-22-tint: rgba(113,86,220,0.12);
          --group-23: #8c56dc; --group-23-tint: rgba(140,86,220,0.12);
          --group-24: #a656dc; --group-24-tint: rgba(166,86,220,0.12);
          --group-25: #c156dc; --group-25-tint: rgba(193,86,220,0.12);
          --group-26: #dc56dc; --group-26-tint: rgba(220,86,220,0.12);
          --group-27: #dc56c1; --group-27-tint: rgba(220,86,193,0.12);
          --group-28: #dc56a6; --group-28-tint: rgba(220,86,166,0.12);
          --group-29: #dc568c; --group-29-tint: rgba(220,86,140,0.12);
          --group-30: #dc5671; --group-30-tint: rgba(220,86,113,0.12);
          --group-none: #898781; --group-none-tint: transparent;
          --group-other: #c4c2bb;
        }
      }
      .trend-container {
        padding: 12px 16px;
        border-bottom: 1px solid var(--border);
        background: var(--surface-1);
      }
      /* The sticky offset lives on the WRAPPER, not on .trend-container
         itself (2026-08-13 fix). .trend-container's sticky containing block
         used to be its own `.period-block` wrapper, and that wrapper is
         exactly the container's own height - zero sticky travel, so `top`
         was declared but never had anywhere to go and the bar never stuck.
         `body > .period-block` selects only the trend period-blocks: they
         are the one set of the three `.period-block` groups (trend,
         Panorama, composition legend - see render_html) rendered as direct
         children of <body>; the other two sit inside the tab panes. That
         makes the selector do the scoping instead of a new class. */
      body > .period-block { position: sticky; top: var(--period-filter-height); z-index: 5; }
      .trend-mode-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
      .trend-pane { display: none; }
      .trend-pane.active { display: block; }
      .trend-scroll { overflow-x: auto; max-width: 100%; }
      .trend-svg { height: 150px; display: block; margin: 0 auto; }
      .chart-highlight-band { fill: var(--series-1); opacity: 0.06; }
      /* Diagonal hatch drawn over the trend chart's last (still-running)
         bucket, so it reads as "in progress" rather than a flat, seemingly
         final bar like every complete one beside it (backlog, 2026-09-02:
         "the newest bar always reads as a decline"). */
      .chart-bar-partial { pointer-events: none; }
      .chart-hatch-bg { fill: transparent; }
      .chart-hatch-line { stroke: var(--surface-1); stroke-width: 3; opacity: 0.6; }
      .project-chart-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .project-chart-title { font-size: 13px; margin: 0 0 8px; color: var(--text-secondary); }
      .project-chart-sort-note { font-size: 10px; margin: -4px 0 8px; color: var(--text-muted); }
      .project-chart-svg { width: 100%; max-width: 760px; display: block; }
      .project-chart-label {
        font-size: 10px;
        fill: var(--text-primary);
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      }
      .project-chart-track { fill: var(--border); }
      .trend-legend-slot { height: 52px; overflow-y: auto; }
      .project-chart-legend {
        display: flex;
        flex-wrap: wrap;
        row-gap: 4px;
        gap: 14px;
        padding: 6px 0 0;
        font-size: 10px;
        color: var(--text-secondary);
      }
      .project-chart-legend .legend-chip, .trend-legend-slot .legend-chip { cursor: pointer; }
      .project-chart-toggle {
        font-size: 11px;
        color: var(--series-1);
        cursor: pointer;
        padding: 4px 0;
      }
      .project-chart-toggle:hover { text-decoration: underline; }
      .chart-gridline { stroke: var(--border); stroke-width: 1; }
      .chart-axis-label {
        fill: var(--text-muted);
        font-size: 10px;
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      }
      .chart-bar { cursor: pointer; }
      .chart-bar.group-color-1 { fill: var(--group-1); }
      .chart-bar.group-color-2 { fill: var(--group-2); }
      .chart-bar.group-color-3 { fill: var(--group-3); }
      .chart-bar.group-color-4 { fill: var(--group-4); }
      .chart-bar.group-color-5 { fill: var(--group-5); }
      .chart-bar.group-color-6 { fill: var(--group-6); }
      .chart-bar.group-color-7 { fill: var(--group-7); }
      .chart-bar.group-color-8 { fill: var(--group-8); }
      .chart-bar.group-color-9 { fill: var(--group-9); }
      .chart-bar.group-color-10 { fill: var(--group-10); }
      .chart-bar.group-color-11 { fill: var(--group-11); }
      .chart-bar.group-color-12 { fill: var(--group-12); }
      .chart-bar.group-color-13 { fill: var(--group-13); }
      .chart-bar.group-color-14 { fill: var(--group-14); }
      .chart-bar.group-color-15 { fill: var(--group-15); }
      .chart-bar.group-color-16 { fill: var(--group-16); }
      .chart-bar.group-color-17 { fill: var(--group-17); }
      .chart-bar.group-color-18 { fill: var(--group-18); }
      .chart-bar.group-color-19 { fill: var(--group-19); }
      .chart-bar.group-color-20 { fill: var(--group-20); }
      .chart-bar.group-color-21 { fill: var(--group-21); }
      .chart-bar.group-color-22 { fill: var(--group-22); }
      .chart-bar.group-color-23 { fill: var(--group-23); }
      .chart-bar.group-color-24 { fill: var(--group-24); }
      .chart-bar.group-color-25 { fill: var(--group-25); }
      .chart-bar.group-color-26 { fill: var(--group-26); }
      .chart-bar.group-color-27 { fill: var(--group-27); }
      .chart-bar.group-color-28 { fill: var(--group-28); }
      .chart-bar.group-color-29 { fill: var(--group-29); }
      .chart-bar.group-color-30 { fill: var(--group-30); }
      .chart-bar.group-color-none { fill: var(--group-none); }
      /* A model past the colour palette (ModelColorSlot.is_overflow, see
         lib/metrics_core/format.py) reuses an earlier slot's fill instead
         of going uncoloured - same colour var, own class name, so its
         highlight rules below never fire together with the class the
         colour's original owner wears. */
      .chart-bar.model-overflow-1 { fill: var(--group-1); }
      .chart-bar.model-overflow-2 { fill: var(--group-2); }
      .chart-bar.model-overflow-3 { fill: var(--group-3); }
      .chart-bar.model-overflow-4 { fill: var(--group-4); }
      .chart-bar.model-overflow-5 { fill: var(--group-5); }
      .chart-bar.model-overflow-6 { fill: var(--group-6); }
      .chart-bar.model-overflow-7 { fill: var(--group-7); }
      .chart-bar.model-overflow-8 { fill: var(--group-8); }
      .chart-bar.model-overflow-9 { fill: var(--group-9); }
      .chart-bar.model-overflow-10 { fill: var(--group-10); }
      .chart-bar.model-overflow-11 { fill: var(--group-11); }
      .chart-bar.model-overflow-12 { fill: var(--group-12); }
      .chart-bar.model-overflow-13 { fill: var(--group-13); }
      .chart-bar.model-overflow-14 { fill: var(--group-14); }
      .chart-bar.model-overflow-15 { fill: var(--group-15); }
      .chart-bar.model-overflow-16 { fill: var(--group-16); }
      .chart-bar.model-overflow-17 { fill: var(--group-17); }
      .chart-bar.model-overflow-18 { fill: var(--group-18); }
      .chart-bar.model-overflow-19 { fill: var(--group-19); }
      .chart-bar.model-overflow-20 { fill: var(--group-20); }
      .chart-bar.model-overflow-21 { fill: var(--group-21); }
      .chart-bar.model-overflow-22 { fill: var(--group-22); }
      .chart-bar.model-overflow-23 { fill: var(--group-23); }
      .chart-bar.model-overflow-24 { fill: var(--group-24); }
      .chart-bar.model-overflow-25 { fill: var(--group-25); }
      .chart-bar.model-overflow-26 { fill: var(--group-26); }
      .chart-bar.model-overflow-27 { fill: var(--group-27); }
      .chart-bar.model-overflow-28 { fill: var(--group-28); }
      .chart-bar.model-overflow-29 { fill: var(--group-29); }
      .chart-bar.model-overflow-30 { fill: var(--group-30); }
      .chart-bar.series-1 { fill: var(--series-1); }
      .chart-bar.series-2 { fill: var(--series-2); }
      .chart-bar.series-3 { fill: var(--series-3); }
      .chart-bar.series-4 { fill: var(--series-4); }
      body {
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        margin: 0;
        background: var(--surface-1);
        color: var(--text-primary);
      }
      .tab-bar {
        display: flex;
        gap: 4px;
        padding: 8px 16px 0;
        border-bottom: 1px solid var(--border);
      }
      .tab-btn {
        padding: 6px 14px;
        font-size: 12px;
        cursor: pointer;
        color: var(--text-muted);
        border-bottom: 2px solid transparent;
      }
      .tab-btn.active {
        color: var(--text-primary);
        border-bottom-color: var(--series-1);
        font-weight: bold;
      }
      /* Keyboard access (2026-10-05 review): every control is reachable by
         Tab, so focus must be seen. :focus-visible keeps a mouse click from
         drawing the ring. */
      :focus-visible { outline: 2px solid var(--series-1); outline-offset: 2px; }
      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
      }
      .tab-pane { display: none; }
      .tab-pane.active { display: block; }
      .main-row { display: flex; }
      .session-list {
        width: 62%;
        border-right: 1px solid var(--border);
        overflow-x: auto;
        font-family: monospace;
      }
      /* width: fit-content + min-width: 100% (2026-07-21) - a flex row
         with no explicit width stretches to fill .session-list's box
         (62% of the page) but does NOT grow past that to fit overflowing
         children; the row's own background then only covers that
         original width while content (and .session-list's
         overflow-x:auto scrollbar) extends further right, so the
         group-color tint stopped partway across a horizontally-scrolled
         row instead of following the content. fit-content lets the row
         grow to match its actual (wider) content when needed. */
      .session-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        cursor: pointer;
        border-bottom: 1px solid var(--border);
        font-size: 13px;
        width: fit-content;
        min-width: 100%;
      }
      .session-row > span:not(:last-child) {
        border-right: 1px solid var(--border);
        padding-right: 8px;
      }
      .session-row:hover { background: rgba(42, 120, 214, 0.08); }
      .session-row.active { background: rgba(42, 120, 214, 0.16); font-weight: bold; }
      .session-row .sid { color: var(--text-muted); width: 90px; flex-shrink: 0; }
      .session-row .subagent-col { width: 28px; flex-shrink: 0; text-align: center; }
      .session-row .tokens-total {
        color: var(--text-secondary);
        width: 55px;
        flex-shrink: 0;
        text-align: right;
      }
      .session-row .tmux-window {
        width: 120px;
        flex-shrink: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: var(--text-muted);
        font-style: italic;
      }
      .session-header {
        position: sticky;
        top: 0;
        z-index: 1;
        background: var(--surface-1);
        font-weight: bold;
        color: var(--text-muted);
        font-size: 11px;
        cursor: default;
        border-bottom: 2px solid var(--border);
      }
      .session-header:hover { background: var(--surface-1); }
      .session-row .date-col { width: 65px; flex-shrink: 0; }
      .session-row .label-col {
        width: 300px;
        flex-shrink: 0;
        white-space: normal;
        word-break: break-word;
      }
      .session-row .tag-col {
        width: 50px;
        flex-shrink: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .session-row .branch-col {
        width: 110px;
        flex-shrink: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .session-row .duration-col { width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .comp-header { width: 90px; flex-shrink: 0; }
      .quota-line {
        padding: 6px 10px 0;
        font-size: 11px;
        color: var(--text-secondary);
        font-family: monospace;
        font-weight: bold;
      }
      .comp-legend {
        display: flex;
        gap: 14px;
        padding: 8px 10px;
        font-size: 11px;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border);
        font-family: monospace;
      }
      .legend-chip { display: inline-flex; align-items: center; }
      .comp-legend .legend-chip { cursor: pointer; }
      .legend-swatch {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 2px;
        margin-right: 4px;
      }
      .legend-swatch.group-color-1 { background: var(--group-1); }
      .legend-swatch.group-color-2 { background: var(--group-2); }
      .legend-swatch.group-color-3 { background: var(--group-3); }
      .legend-swatch.group-color-4 { background: var(--group-4); }
      .legend-swatch.group-color-5 { background: var(--group-5); }
      .legend-swatch.group-color-6 { background: var(--group-6); }
      .legend-swatch.group-color-7 { background: var(--group-7); }
      .legend-swatch.group-color-8 { background: var(--group-8); }
      .legend-swatch.group-color-9 { background: var(--group-9); }
      .legend-swatch.group-color-10 { background: var(--group-10); }
      .legend-swatch.group-color-11 { background: var(--group-11); }
      .legend-swatch.group-color-12 { background: var(--group-12); }
      .legend-swatch.group-color-13 { background: var(--group-13); }
      .legend-swatch.group-color-14 { background: var(--group-14); }
      .legend-swatch.group-color-15 { background: var(--group-15); }
      .legend-swatch.group-color-16 { background: var(--group-16); }
      .legend-swatch.group-color-17 { background: var(--group-17); }
      .legend-swatch.group-color-18 { background: var(--group-18); }
      .legend-swatch.group-color-19 { background: var(--group-19); }
      .legend-swatch.group-color-20 { background: var(--group-20); }
      .legend-swatch.group-color-21 { background: var(--group-21); }
      .legend-swatch.group-color-22 { background: var(--group-22); }
      .legend-swatch.group-color-23 { background: var(--group-23); }
      .legend-swatch.group-color-24 { background: var(--group-24); }
      .legend-swatch.group-color-25 { background: var(--group-25); }
      .legend-swatch.group-color-26 { background: var(--group-26); }
      .legend-swatch.group-color-27 { background: var(--group-27); }
      .legend-swatch.group-color-28 { background: var(--group-28); }
      .legend-swatch.group-color-29 { background: var(--group-29); }
      .legend-swatch.group-color-30 { background: var(--group-30); }
      .legend-swatch.group-color-none { background: var(--group-none); }
      /* Mirrors the model-overflow-N chart-bar fills above, for the "por
         modelo" legend chip's own swatch. */
      .legend-swatch.model-overflow-1 { background: var(--group-1); }
      .legend-swatch.model-overflow-2 { background: var(--group-2); }
      .legend-swatch.model-overflow-3 { background: var(--group-3); }
      .legend-swatch.model-overflow-4 { background: var(--group-4); }
      .legend-swatch.model-overflow-5 { background: var(--group-5); }
      .legend-swatch.model-overflow-6 { background: var(--group-6); }
      .legend-swatch.model-overflow-7 { background: var(--group-7); }
      .legend-swatch.model-overflow-8 { background: var(--group-8); }
      .legend-swatch.model-overflow-9 { background: var(--group-9); }
      .legend-swatch.model-overflow-10 { background: var(--group-10); }
      .legend-swatch.model-overflow-11 { background: var(--group-11); }
      .legend-swatch.model-overflow-12 { background: var(--group-12); }
      .legend-swatch.model-overflow-13 { background: var(--group-13); }
      .legend-swatch.model-overflow-14 { background: var(--group-14); }
      .legend-swatch.model-overflow-15 { background: var(--group-15); }
      .legend-swatch.model-overflow-16 { background: var(--group-16); }
      .legend-swatch.model-overflow-17 { background: var(--group-17); }
      .legend-swatch.model-overflow-18 { background: var(--group-18); }
      .legend-swatch.model-overflow-19 { background: var(--group-19); }
      .legend-swatch.model-overflow-20 { background: var(--group-20); }
      .legend-swatch.model-overflow-21 { background: var(--group-21); }
      .legend-swatch.model-overflow-22 { background: var(--group-22); }
      .legend-swatch.model-overflow-23 { background: var(--group-23); }
      .legend-swatch.model-overflow-24 { background: var(--group-24); }
      .legend-swatch.model-overflow-25 { background: var(--group-25); }
      .legend-swatch.model-overflow-26 { background: var(--group-26); }
      .legend-swatch.model-overflow-27 { background: var(--group-27); }
      .legend-swatch.model-overflow-28 { background: var(--group-28); }
      .legend-swatch.model-overflow-29 { background: var(--group-29); }
      .legend-swatch.model-overflow-30 { background: var(--group-30); }
      .legend-swatch.series-1 { background: var(--series-1); }
      .legend-swatch.series-2 { background: var(--series-2); }
      .legend-swatch.series-3 { background: var(--series-3); }
      .legend-swatch.series-4 { background: var(--series-4); }
      .legend-total { font-weight: 600; }
      body[data-highlight] .legend-chip { opacity: 0.4; }
      body[data-highlight] .legend-chip .legend-total { font-weight: 400; }
      body[data-highlight~="series-1"] .legend-chip[data-series="series-1"],
      body[data-highlight~="series-2"] .legend-chip[data-series="series-2"],
      body[data-highlight~="series-3"] .legend-chip[data-series="series-3"],
      body[data-highlight~="series-4"] .legend-chip[data-series="series-4"] {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="series-1"] .legend-chip[data-series="series-1"] .legend-total,
      body[data-highlight~="series-2"] .legend-chip[data-series="series-2"] .legend-total,
      body[data-highlight~="series-3"] .legend-chip[data-series="series-3"] .legend-total,
      body[data-highlight~="series-4"] .legend-chip[data-series="series-4"] .legend-total {
        font-weight: 700;
        font-size: 13px;
        opacity: 1;
      }
      /* Spotlight pattern: dim everything, then un-dim the highlighted
         series. For .comp-seg the un-dim rule outranks this one on
         specificity alone (extra .series-N class); for .chart-bar.series-N
         the two tie on specificity and the un-dim rule wins only because
         it's declared AFTER this one - keep this block before the un-dim
         rules below, do not reorder. */
      body[data-highlight] .comp-seg,
      body[data-highlight] .chart-bar.series-1,
      body[data-highlight] .chart-bar.series-2,
      body[data-highlight] .chart-bar.series-3,
      body[data-highlight] .chart-bar.series-4 {
        opacity: 0.25;
      }
      body[data-highlight~="series-1"] .comp-seg.series-1,
      body[data-highlight~="series-1"] .chart-bar.series-1 {
        opacity: 1;
      }
      body[data-highlight~="series-2"] .comp-seg.series-2,
      body[data-highlight~="series-2"] .chart-bar.series-2 {
        opacity: 1;
      }
      body[data-highlight~="series-3"] .comp-seg.series-3,
      body[data-highlight~="series-3"] .chart-bar.series-3 {
        opacity: 1;
      }
      body[data-highlight~="series-4"] .comp-seg.series-4,
      body[data-highlight~="series-4"] .chart-bar.series-4 {
        opacity: 1;
      }
      body[data-highlight~="series-1"] .comp-seg.series-1 {
        outline: 1px solid var(--series-1);
        outline-offset: 1px;
      }
      body[data-highlight~="series-2"] .comp-seg.series-2 {
        outline: 1px solid var(--series-2);
        outline-offset: 1px;
      }
      body[data-highlight~="series-3"] .comp-seg.series-3 {
        outline: 1px solid var(--series-3);
        outline-offset: 1px;
      }
      body[data-highlight~="series-4"] .comp-seg.series-4 {
        outline: 1px solid var(--series-4);
        outline-offset: 1px;
      }
      body[data-highlight~="series-1"] .chart-bar.series-1 {
        stroke: var(--series-1);
        stroke-width: 1.5px;
      }
      body[data-highlight~="series-2"] .chart-bar.series-2 {
        stroke: var(--series-2);
        stroke-width: 1.5px;
      }
      body[data-highlight~="series-3"] .chart-bar.series-3 {
        stroke: var(--series-3);
        stroke-width: 1.5px;
      }
      body[data-highlight~="series-4"] .chart-bar.series-4 {
        stroke: var(--series-4);
        stroke-width: 1.5px;
      }
      /* Same spotlight pattern as series-N above, for "por projeto"'s
         group-color-N bars/legend (2026-07-21) - the two share one
         data-highlight attribute since only one trend-mode pane is ever
         visible at once. */
      body[data-highlight] .chart-bar.group-color-1,
      body[data-highlight] .chart-bar.group-color-2,
      body[data-highlight] .chart-bar.group-color-3,
      body[data-highlight] .chart-bar.group-color-4,
      body[data-highlight] .chart-bar.group-color-5,
      body[data-highlight] .chart-bar.group-color-6,
      body[data-highlight] .chart-bar.group-color-7,
      body[data-highlight] .chart-bar.group-color-8,
      body[data-highlight] .chart-bar.group-color-9,
      body[data-highlight] .chart-bar.group-color-10,
      body[data-highlight] .chart-bar.group-color-11,
      body[data-highlight] .chart-bar.group-color-12,
      body[data-highlight] .chart-bar.group-color-13,
      body[data-highlight] .chart-bar.group-color-14,
      body[data-highlight] .chart-bar.group-color-15,
      body[data-highlight] .chart-bar.group-color-16,
      body[data-highlight] .chart-bar.group-color-17,
      body[data-highlight] .chart-bar.group-color-18,
      body[data-highlight] .chart-bar.group-color-19,
      body[data-highlight] .chart-bar.group-color-20,
      body[data-highlight] .chart-bar.group-color-21,
      body[data-highlight] .chart-bar.group-color-22,
      body[data-highlight] .chart-bar.group-color-23,
      body[data-highlight] .chart-bar.group-color-24,
      body[data-highlight] .chart-bar.group-color-25,
      body[data-highlight] .chart-bar.group-color-26,
      body[data-highlight] .chart-bar.group-color-27,
      body[data-highlight] .chart-bar.group-color-28,
      body[data-highlight] .chart-bar.group-color-29,
      body[data-highlight] .chart-bar.group-color-30,
      body[data-highlight] .chart-bar.group-color-none {
        opacity: 0.25;
      }
      /* model-overflow-N's dimming, kept OUT of the combined list above on
         purpose: jsdom's CSS engine (measured 2026-09-29, backlog sweep 3 /
         M1) silently stops matching a handful of selectors in a comma list
         once it grows long enough - doubling that list from 31 to 61
         entries by appending model-overflow-1..30 there left "group-color-
         none" undimmed in the rendering test. A substring attribute match
         says the same thing in one selector instead of thirty. */
      body[data-highlight] .chart-bar[class*="model-overflow-"] {
        opacity: 0.25;
      }
      body[data-highlight~="group-color-1"] .chart-bar.group-color-1 {
        opacity: 1;
        stroke: var(--group-1);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-2"] .chart-bar.group-color-2 {
        opacity: 1;
        stroke: var(--group-2);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-3"] .chart-bar.group-color-3 {
        opacity: 1;
        stroke: var(--group-3);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-4"] .chart-bar.group-color-4 {
        opacity: 1;
        stroke: var(--group-4);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-5"] .chart-bar.group-color-5 {
        opacity: 1;
        stroke: var(--group-5);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-6"] .chart-bar.group-color-6 {
        opacity: 1;
        stroke: var(--group-6);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-7"] .chart-bar.group-color-7 {
        opacity: 1;
        stroke: var(--group-7);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-8"] .chart-bar.group-color-8 {
        opacity: 1;
        stroke: var(--group-8);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-9"] .chart-bar.group-color-9 {
        opacity: 1;
        stroke: var(--group-9);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-10"] .chart-bar.group-color-10 {
        opacity: 1;
        stroke: var(--group-10);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-11"] .chart-bar.group-color-11 {
        opacity: 1;
        stroke: var(--group-11);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-12"] .chart-bar.group-color-12 {
        opacity: 1;
        stroke: var(--group-12);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-13"] .chart-bar.group-color-13 {
        opacity: 1;
        stroke: var(--group-13);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-14"] .chart-bar.group-color-14 {
        opacity: 1;
        stroke: var(--group-14);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-15"] .chart-bar.group-color-15 {
        opacity: 1;
        stroke: var(--group-15);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-16"] .chart-bar.group-color-16 {
        opacity: 1;
        stroke: var(--group-16);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-17"] .chart-bar.group-color-17 {
        opacity: 1;
        stroke: var(--group-17);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-18"] .chart-bar.group-color-18 {
        opacity: 1;
        stroke: var(--group-18);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-19"] .chart-bar.group-color-19 {
        opacity: 1;
        stroke: var(--group-19);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-20"] .chart-bar.group-color-20 {
        opacity: 1;
        stroke: var(--group-20);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-21"] .chart-bar.group-color-21 {
        opacity: 1;
        stroke: var(--group-21);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-22"] .chart-bar.group-color-22 {
        opacity: 1;
        stroke: var(--group-22);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-23"] .chart-bar.group-color-23 {
        opacity: 1;
        stroke: var(--group-23);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-24"] .chart-bar.group-color-24 {
        opacity: 1;
        stroke: var(--group-24);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-25"] .chart-bar.group-color-25 {
        opacity: 1;
        stroke: var(--group-25);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-26"] .chart-bar.group-color-26 {
        opacity: 1;
        stroke: var(--group-26);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-27"] .chart-bar.group-color-27 {
        opacity: 1;
        stroke: var(--group-27);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-28"] .chart-bar.group-color-28 {
        opacity: 1;
        stroke: var(--group-28);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-29"] .chart-bar.group-color-29 {
        opacity: 1;
        stroke: var(--group-29);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-30"] .chart-bar.group-color-30 {
        opacity: 1;
        stroke: var(--group-30);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-none"] .chart-bar.group-color-none {
        opacity: 1;
        stroke: var(--group-none);
        stroke-width: 1.5px;
      }
      /* model-overflow-N's own spotlight rule - deliberately separate from
         group-color-N's above, even though N shares a fill colour with it,
         so clicking an overflow model's band or chip never also lights up
         the real model whose colour it borrowed (or another overflow
         model that happens to share the same cycled colour). */
      body[data-highlight~="model-overflow-1"] .chart-bar.model-overflow-1 {
        opacity: 1;
        stroke: var(--group-1);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-2"] .chart-bar.model-overflow-2 {
        opacity: 1;
        stroke: var(--group-2);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-3"] .chart-bar.model-overflow-3 {
        opacity: 1;
        stroke: var(--group-3);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-4"] .chart-bar.model-overflow-4 {
        opacity: 1;
        stroke: var(--group-4);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-5"] .chart-bar.model-overflow-5 {
        opacity: 1;
        stroke: var(--group-5);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-6"] .chart-bar.model-overflow-6 {
        opacity: 1;
        stroke: var(--group-6);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-7"] .chart-bar.model-overflow-7 {
        opacity: 1;
        stroke: var(--group-7);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-8"] .chart-bar.model-overflow-8 {
        opacity: 1;
        stroke: var(--group-8);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-9"] .chart-bar.model-overflow-9 {
        opacity: 1;
        stroke: var(--group-9);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-10"] .chart-bar.model-overflow-10 {
        opacity: 1;
        stroke: var(--group-10);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-11"] .chart-bar.model-overflow-11 {
        opacity: 1;
        stroke: var(--group-11);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-12"] .chart-bar.model-overflow-12 {
        opacity: 1;
        stroke: var(--group-12);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-13"] .chart-bar.model-overflow-13 {
        opacity: 1;
        stroke: var(--group-13);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-14"] .chart-bar.model-overflow-14 {
        opacity: 1;
        stroke: var(--group-14);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-15"] .chart-bar.model-overflow-15 {
        opacity: 1;
        stroke: var(--group-15);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-16"] .chart-bar.model-overflow-16 {
        opacity: 1;
        stroke: var(--group-16);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-17"] .chart-bar.model-overflow-17 {
        opacity: 1;
        stroke: var(--group-17);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-18"] .chart-bar.model-overflow-18 {
        opacity: 1;
        stroke: var(--group-18);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-19"] .chart-bar.model-overflow-19 {
        opacity: 1;
        stroke: var(--group-19);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-20"] .chart-bar.model-overflow-20 {
        opacity: 1;
        stroke: var(--group-20);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-21"] .chart-bar.model-overflow-21 {
        opacity: 1;
        stroke: var(--group-21);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-22"] .chart-bar.model-overflow-22 {
        opacity: 1;
        stroke: var(--group-22);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-23"] .chart-bar.model-overflow-23 {
        opacity: 1;
        stroke: var(--group-23);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-24"] .chart-bar.model-overflow-24 {
        opacity: 1;
        stroke: var(--group-24);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-25"] .chart-bar.model-overflow-25 {
        opacity: 1;
        stroke: var(--group-25);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-26"] .chart-bar.model-overflow-26 {
        opacity: 1;
        stroke: var(--group-26);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-27"] .chart-bar.model-overflow-27 {
        opacity: 1;
        stroke: var(--group-27);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-28"] .chart-bar.model-overflow-28 {
        opacity: 1;
        stroke: var(--group-28);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-29"] .chart-bar.model-overflow-29 {
        opacity: 1;
        stroke: var(--group-29);
        stroke-width: 1.5px;
      }
      body[data-highlight~="model-overflow-30"] .chart-bar.model-overflow-30 {
        opacity: 1;
        stroke: var(--group-30);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-1"] .legend-chip:has(.legend-swatch.group-color-1),
      body[data-highlight~="group-color-2"] .legend-chip:has(.legend-swatch.group-color-2),
      body[data-highlight~="group-color-3"] .legend-chip:has(.legend-swatch.group-color-3),
      body[data-highlight~="group-color-4"] .legend-chip:has(.legend-swatch.group-color-4),
      body[data-highlight~="group-color-5"] .legend-chip:has(.legend-swatch.group-color-5),
      body[data-highlight~="group-color-6"] .legend-chip:has(.legend-swatch.group-color-6),
      body[data-highlight~="group-color-7"] .legend-chip:has(.legend-swatch.group-color-7),
      body[data-highlight~="group-color-8"] .legend-chip:has(.legend-swatch.group-color-8),
      body[data-highlight~="group-color-9"] .legend-chip:has(.legend-swatch.group-color-9),
      body[data-highlight~="group-color-10"] .legend-chip:has(.legend-swatch.group-color-10),
      body[data-highlight~="group-color-11"] .legend-chip:has(.legend-swatch.group-color-11),
      body[data-highlight~="group-color-12"] .legend-chip:has(.legend-swatch.group-color-12),
      body[data-highlight~="group-color-13"] .legend-chip:has(.legend-swatch.group-color-13),
      body[data-highlight~="group-color-14"] .legend-chip:has(.legend-swatch.group-color-14),
      body[data-highlight~="group-color-15"] .legend-chip:has(.legend-swatch.group-color-15),
      body[data-highlight~="group-color-16"] .legend-chip:has(.legend-swatch.group-color-16),
      body[data-highlight~="group-color-17"] .legend-chip:has(.legend-swatch.group-color-17),
      body[data-highlight~="group-color-18"] .legend-chip:has(.legend-swatch.group-color-18),
      body[data-highlight~="group-color-19"] .legend-chip:has(.legend-swatch.group-color-19),
      body[data-highlight~="group-color-20"] .legend-chip:has(.legend-swatch.group-color-20),
      body[data-highlight~="group-color-21"] .legend-chip:has(.legend-swatch.group-color-21),
      body[data-highlight~="group-color-22"] .legend-chip:has(.legend-swatch.group-color-22),
      body[data-highlight~="group-color-23"] .legend-chip:has(.legend-swatch.group-color-23),
      body[data-highlight~="group-color-24"] .legend-chip:has(.legend-swatch.group-color-24),
      body[data-highlight~="group-color-25"] .legend-chip:has(.legend-swatch.group-color-25),
      body[data-highlight~="group-color-26"] .legend-chip:has(.legend-swatch.group-color-26),
      body[data-highlight~="group-color-27"] .legend-chip:has(.legend-swatch.group-color-27),
      body[data-highlight~="group-color-28"] .legend-chip:has(.legend-swatch.group-color-28),
      body[data-highlight~="group-color-29"] .legend-chip:has(.legend-swatch.group-color-29),
      body[data-highlight~="group-color-30"] .legend-chip:has(.legend-swatch.group-color-30) {
        opacity: 1;
        font-weight: 700;
      }
      /* model-overflow-N's own legend-chip rule, one PER slot rather than
         appended to the combined list above: jsdom's CSS engine (measured
         2026-09-29, backlog sweep 3 / M1) silently stops matching a
         handful of selectors once a comma list grows long enough, and
         appending 30 more here reproduced the exact same failure the
         chart-bar dim rule had (see the .chart-bar[class*="model-
         overflow-"] rule above) - a legend chip staying dim instead of
         bolding when its own model was clicked. Thirty small rules avoid
         the list-length cliff entirely; :has() still needs to be
         PER-N, this one cannot collapse into a single substring selector
         the way the DIM rule did, since every N must pair with its own
         data-highlight token, not any overflow token. */
      body[data-highlight~="model-overflow-1"] .legend-chip:has(.legend-swatch.model-overflow-1) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-2"] .legend-chip:has(.legend-swatch.model-overflow-2) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-3"] .legend-chip:has(.legend-swatch.model-overflow-3) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-4"] .legend-chip:has(.legend-swatch.model-overflow-4) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-5"] .legend-chip:has(.legend-swatch.model-overflow-5) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-6"] .legend-chip:has(.legend-swatch.model-overflow-6) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-7"] .legend-chip:has(.legend-swatch.model-overflow-7) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-8"] .legend-chip:has(.legend-swatch.model-overflow-8) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-9"] .legend-chip:has(.legend-swatch.model-overflow-9) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-10"]
        .legend-chip:has(.legend-swatch.model-overflow-10) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-11"]
        .legend-chip:has(.legend-swatch.model-overflow-11) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-12"]
        .legend-chip:has(.legend-swatch.model-overflow-12) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-13"]
        .legend-chip:has(.legend-swatch.model-overflow-13) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-14"]
        .legend-chip:has(.legend-swatch.model-overflow-14) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-15"]
        .legend-chip:has(.legend-swatch.model-overflow-15) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-16"]
        .legend-chip:has(.legend-swatch.model-overflow-16) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-17"]
        .legend-chip:has(.legend-swatch.model-overflow-17) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-18"]
        .legend-chip:has(.legend-swatch.model-overflow-18) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-19"]
        .legend-chip:has(.legend-swatch.model-overflow-19) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-20"]
        .legend-chip:has(.legend-swatch.model-overflow-20) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-21"]
        .legend-chip:has(.legend-swatch.model-overflow-21) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-22"]
        .legend-chip:has(.legend-swatch.model-overflow-22) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-23"]
        .legend-chip:has(.legend-swatch.model-overflow-23) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-24"]
        .legend-chip:has(.legend-swatch.model-overflow-24) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-25"]
        .legend-chip:has(.legend-swatch.model-overflow-25) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-26"]
        .legend-chip:has(.legend-swatch.model-overflow-26) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-27"]
        .legend-chip:has(.legend-swatch.model-overflow-27) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-28"]
        .legend-chip:has(.legend-swatch.model-overflow-28) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-29"]
        .legend-chip:has(.legend-swatch.model-overflow-29) {
        opacity: 1;
        font-weight: 700;
      }
      body[data-highlight~="model-overflow-30"]
        .legend-chip:has(.legend-swatch.model-overflow-30) {
        opacity: 1;
        font-weight: 700;
      }
      .proj-total .total-only { display: none; }
      body[data-highlight~="series-1"] .proj-total .total-only.series-1 {
        display: inline;
        font-style: italic;
        fill: var(--series-1);
      }
      body[data-highlight~="series-2"] .proj-total .total-only.series-2 {
        display: inline;
        font-style: italic;
        fill: var(--series-2);
      }
      body[data-highlight~="series-3"] .proj-total .total-only.series-3 {
        display: inline;
        font-style: italic;
        fill: var(--series-3);
      }
      body[data-highlight~="series-4"] .proj-total .total-only.series-4 {
        display: inline;
        font-style: italic;
        fill: var(--series-4);
      }
      .comp-bar {
        display: flex;
        gap: 2px;
        width: 90px;
        height: 10px;
        flex-shrink: 0;
        background: var(--border);
      }
      .comp-bar-empty { background: var(--border); }
      .comp-seg.series-1 { background: var(--series-1); }
      .comp-seg.series-2 { background: var(--series-2); }
      .comp-seg.series-3 { background: var(--series-3); }
      .comp-seg.series-4 { background: var(--series-4); }
      .session-row.group-color-1 {
        background: var(--group-1-tint);
        border-left: 3px solid var(--group-1);
      }
      .session-row.group-color-2 {
        background: var(--group-2-tint);
        border-left: 3px solid var(--group-2);
      }
      .session-row.group-color-3 {
        background: var(--group-3-tint);
        border-left: 3px solid var(--group-3);
      }
      .session-row.group-color-4 {
        background: var(--group-4-tint);
        border-left: 3px solid var(--group-4);
      }
      .session-row.group-color-5 {
        background: var(--group-5-tint);
        border-left: 3px solid var(--group-5);
      }
      .session-row.group-color-6 {
        background: var(--group-6-tint);
        border-left: 3px solid var(--group-6);
      }
      .session-row.group-color-7 {
        background: var(--group-7-tint);
        border-left: 3px solid var(--group-7);
      }
      .session-row.group-color-8 {
        background: var(--group-8-tint);
        border-left: 3px solid var(--group-8);
      }
      .session-row.group-color-9 {
        background: var(--group-9-tint);
        border-left: 3px solid var(--group-9);
      }
      .session-row.group-color-10 {
        background: var(--group-10-tint);
        border-left: 3px solid var(--group-10);
      }
      .session-row.group-color-11 {
        background: var(--group-11-tint);
        border-left: 3px solid var(--group-11);
      }
      .session-row.group-color-12 {
        background: var(--group-12-tint);
        border-left: 3px solid var(--group-12);
      }
      .session-row.group-color-13 {
        background: var(--group-13-tint);
        border-left: 3px solid var(--group-13);
      }
      .session-row.group-color-14 {
        background: var(--group-14-tint);
        border-left: 3px solid var(--group-14);
      }
      .session-row.group-color-15 {
        background: var(--group-15-tint);
        border-left: 3px solid var(--group-15);
      }
      .session-row.group-color-16 {
        background: var(--group-16-tint);
        border-left: 3px solid var(--group-16);
      }
      .session-row.group-color-17 {
        background: var(--group-17-tint);
        border-left: 3px solid var(--group-17);
      }
      .session-row.group-color-18 {
        background: var(--group-18-tint);
        border-left: 3px solid var(--group-18);
      }
      .session-row.group-color-19 {
        background: var(--group-19-tint);
        border-left: 3px solid var(--group-19);
      }
      .session-row.group-color-20 {
        background: var(--group-20-tint);
        border-left: 3px solid var(--group-20);
      }
      .session-row.group-color-21 {
        background: var(--group-21-tint);
        border-left: 3px solid var(--group-21);
      }
      .session-row.group-color-22 {
        background: var(--group-22-tint);
        border-left: 3px solid var(--group-22);
      }
      .session-row.group-color-23 {
        background: var(--group-23-tint);
        border-left: 3px solid var(--group-23);
      }
      .session-row.group-color-24 {
        background: var(--group-24-tint);
        border-left: 3px solid var(--group-24);
      }
      .session-row.group-color-25 {
        background: var(--group-25-tint);
        border-left: 3px solid var(--group-25);
      }
      .session-row.group-color-26 {
        background: var(--group-26-tint);
        border-left: 3px solid var(--group-26);
      }
      .session-row.group-color-27 {
        background: var(--group-27-tint);
        border-left: 3px solid var(--group-27);
      }
      .session-row.group-color-28 {
        background: var(--group-28-tint);
        border-left: 3px solid var(--group-28);
      }
      .session-row.group-color-29 {
        background: var(--group-29-tint);
        border-left: 3px solid var(--group-29);
      }
      .session-row.group-color-30 {
        background: var(--group-30-tint);
        border-left: 3px solid var(--group-30);
      }
      .session-row.group-color-none { border-left: 3px solid var(--group-none); }
      .group-header {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 10px;
        font-size: 11px;
        font-weight: bold;
        cursor: pointer;
        border-bottom: 1px solid var(--border);
        background: var(--surface-1);
        width: fit-content;
        min-width: 100%;
      }
      .group-header.group-color-1 { border-left: 3px solid var(--group-1); }
      .group-header.group-color-2 { border-left: 3px solid var(--group-2); }
      .group-header.group-color-3 { border-left: 3px solid var(--group-3); }
      .group-header.group-color-4 { border-left: 3px solid var(--group-4); }
      .group-header.group-color-5 { border-left: 3px solid var(--group-5); }
      .group-header.group-color-6 { border-left: 3px solid var(--group-6); }
      .group-header.group-color-7 { border-left: 3px solid var(--group-7); }
      .group-header.group-color-8 { border-left: 3px solid var(--group-8); }
      .group-header.group-color-9 { border-left: 3px solid var(--group-9); }
      .group-header.group-color-10 { border-left: 3px solid var(--group-10); }
      .group-header.group-color-11 { border-left: 3px solid var(--group-11); }
      .group-header.group-color-12 { border-left: 3px solid var(--group-12); }
      .group-header.group-color-13 { border-left: 3px solid var(--group-13); }
      .group-header.group-color-14 { border-left: 3px solid var(--group-14); }
      .group-header.group-color-15 { border-left: 3px solid var(--group-15); }
      .group-header.group-color-16 { border-left: 3px solid var(--group-16); }
      .group-header.group-color-17 { border-left: 3px solid var(--group-17); }
      .group-header.group-color-18 { border-left: 3px solid var(--group-18); }
      .group-header.group-color-19 { border-left: 3px solid var(--group-19); }
      .group-header.group-color-20 { border-left: 3px solid var(--group-20); }
      .group-header.group-color-21 { border-left: 3px solid var(--group-21); }
      .group-header.group-color-22 { border-left: 3px solid var(--group-22); }
      .group-header.group-color-23 { border-left: 3px solid var(--group-23); }
      .group-header.group-color-24 { border-left: 3px solid var(--group-24); }
      .group-header.group-color-25 { border-left: 3px solid var(--group-25); }
      .group-header.group-color-26 { border-left: 3px solid var(--group-26); }
      .group-header.group-color-27 { border-left: 3px solid var(--group-27); }
      .group-header.group-color-28 { border-left: 3px solid var(--group-28); }
      .group-header.group-color-29 { border-left: 3px solid var(--group-29); }
      .group-header.group-color-30 { border-left: 3px solid var(--group-30); }
      .group-header.group-color-none { border-left: 3px solid var(--group-none); }
      /* The "others" bucket's own rules, standalone rather than appended to
         the long comma lists: jsdom silently stops matching selectors in a
         long enough list (see the model-overflow-N notes below). */
      .chart-bar.group-color-other { fill: var(--group-other); }
      .legend-swatch.group-color-other { background: var(--group-other); }
      body[data-highlight] .chart-bar.group-color-other { opacity: 0.25; }
      body[data-highlight~="group-color-other"] .chart-bar.group-color-other {
        opacity: 1;
        stroke: var(--group-other);
        stroke-width: 1.5px;
      }
      body[data-highlight~="group-color-other"] .legend-chip:has(.legend-swatch.group-color-other) {
        opacity: 1;
        font-weight: 700;
      }
      .session-row.group-color-other { border-left: 3px solid var(--group-other); }
      .group-header.group-color-other { border-left: 3px solid var(--group-other); }
      .group-icon { width: 10px; display: inline-block; }
      .detail-container { flex: 1; }
      .detail-panel { display: none; padding: 16px; font-family: monospace; }
      .detail-panel.open { display: block; }
      /* Higher specificity (3 classes) than .detail-panel.open (2 classes)
         wins on its own, no !important needed - an open panel whose
         session just left the active period still disappears. */
      .detail-panel.open.period-hidden { display: none; }
      .detail-close {
        float: right;
        background: none;
        border: none;
        font-size: 18px;
        cursor: pointer;
        color: var(--text-muted);
        line-height: 1;
        padding: 0;
      }
      .detail-close:hover { color: var(--text-primary); }
      .subagent-row {
        display: grid;
        grid-template-columns: 0.6fr minmax(200px, 1.6fr) 0.9fr 0.6fr 0.7fr;
        padding: 4px 8px;
        border-bottom: 1px solid var(--border);
      }
      .subagent-tokens-cell { display: flex; align-items: center; gap: 6px; }
      .floor-total { color: var(--text-secondary); }
      .subagent-row > span:not(:last-child) {
        border-right: 1px solid var(--border);
        padding-right: 8px;
      }
      .subagent-row > span:nth-child(2) { white-space: normal; word-break: break-word; }
      .subagent-title { font-size: 12px; margin: 12px 0 6px; color: var(--text-secondary); }
      .subagent-row.subagent-header {
        font-weight: bold;
        color: var(--text-muted);
        font-size: 10px;
        border-bottom: 2px solid var(--border);
      }
      .subagent-row-odd { background: rgba(0,0,0,0.02); }
      .empty { color: var(--text-muted); }
      .period-filter {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 16px;
        height: var(--period-filter-height);
        box-sizing: border-box;
        position: sticky;
        top: 0;
        z-index: 20;
        background: var(--surface-1);
      }
      .period-chip {
        cursor: pointer;
        border: 1px solid currentColor;
        border-radius: 999px;
        padding: 4px 12px;
        font: inherit;
        background: none;
        color: inherit;
        opacity: 0.55;
      }
      .period-chip.active { opacity: 1; font-weight: 600; }
      .backfill-note {
        margin-left: auto;
        font-size: 11px;
        color: var(--text-muted);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .period-block[hidden] { display: none; }
      /* The only two rules that hide a session row, both class-driven and
         both display:none - no inline style competes with either, so they
         compose instead of fighting (see render_session_row). */
      .session-row.collapsed { display: none; }
      .session-row.period-hidden { display: none; }
      .group-header.period-hidden { display: none; }
    </style>
    """

SCRIPT = """
    <script>
    function showSession(id) {
      var row = document.getElementById('row-' + id);
      if (row) {
        var groupId = row.getAttribute('data-group');
        var header = document.getElementById('group-' + groupId);
        if (header && header.classList.contains('collapsed')) {
          toggleGroup(parseInt(groupId, 10));
        }
      }
      document.querySelectorAll('.detail-panel').forEach(function(el) {
        el.classList.remove('open');
      });
      document.querySelectorAll('.session-row').forEach(function(el) {
        el.classList.remove('active');
      });
      var panel = document.getElementById('detail-' + id);
      if (panel) { panel.classList.add('open'); }
      if (row) { row.classList.add('active'); }
    }
    function closeDetail(id) {
      var panel = document.getElementById('detail-' + id);
      if (panel) { panel.classList.remove('open'); }
      var row = document.getElementById('row-' + id);
      if (row) { row.classList.remove('active'); }
    }
    function showTab(name) {
      document.querySelectorAll('.tab-pane').forEach(function(el) {
        el.classList.remove('active');
      });
      // Scoped to .tab-bar's own buttons - .trend-mode-tabs reuses the
      // same .tab-btn class for its visual style and has its own
      // independent active state (showTrendMode below).
      document.querySelectorAll('.tab-bar .tab-btn').forEach(function(el) {
        el.classList.remove('active');
        el.setAttribute('aria-selected', 'false');
      });
      var pane = document.getElementById('tab-' + name);
      var btn = document.getElementById('tab-btn-' + name);
      if (pane) { pane.classList.add('active'); }
      if (btn) { btn.classList.add('active'); btn.setAttribute('aria-selected', 'true'); }
    }
    function showTrendMode(name, periodId) {
      // ids are suffixed with periodId (render_trend_chart runs once per
      // period) so this always resolves the pane/button belonging to the
      // chart that was actually clicked, not just the first period's.
      var pane = document.getElementById('trend-pane-' + name + '-' + periodId);
      var btn = document.getElementById('trend-btn-' + name + '-' + periodId);
      // Clearing is scoped to THIS period's own .trend-container, found via
      // closest() on the clicked pane/button rather than by re-deriving it
      // from periodId - each period has its own trend-mode-tabs/trend-pane
      // pair, and there are two other kinds of .period-block sharing the
      // same data-period (panorama, composition legend), so a lookup keyed
      // only on data-period could silently latch onto the wrong one if
      // their document order ever changed. A document-wide clear here used
      // to strip 'active' from every OTHER period's pane/button too,
      // leaving them with no active pane at all the next time the reader
      // switched to one (2026-08-18 fix - "the chart disappeared for
      // every one except the filter I was on").
      var container = (pane || btn) && (pane || btn).closest('.trend-container');
      if (!container) { return; }
      // A highlight belongs to the slicing it was made in. The three modes
      // share one data-highlight vocabulary, so carrying a selection across
      // a mode switch would dim the new pane by a class that means
      // something else there (a project's colour slot vs a model's).
      document.body.removeAttribute('data-highlight');
      container.querySelectorAll('.trend-pane').forEach(function(el) {
        el.classList.remove('active');
      });
      container.querySelectorAll('.trend-mode-tabs .tab-btn').forEach(function(el) {
        el.classList.remove('active');
        el.setAttribute('aria-selected', 'false');
      });
      if (pane) { pane.classList.add('active'); }
      if (btn) { btn.classList.add('active'); btn.setAttribute('aria-selected', 'true'); }
      syncPressed();
    }
    function toggleProjectsMore(periodId) {
      var svg = document.getElementById('project-chart-more-' + periodId);
      var toggle = document.getElementById('project-chart-toggle-' + periodId);
      if (!svg) { return; }
      var hidden = svg.style.display === 'none';
      svg.style.display = hidden ? 'block' : 'none';
      if (toggle) { toggle.classList.toggle('expanded', hidden); }
    }
    function toggleGroup(id) {
      var header = document.getElementById('group-' + id);
      if (!header) { return; }
      var collapsed = header.classList.contains('collapsed');
      var rows = document.querySelectorAll('.session-row[data-group="' + id + '"]');
      // Toggles a class rather than writing an inline display, so a row the
      // period filter has hidden stays hidden through an expand/collapse and
      // the two mechanisms never overwrite each other.
      rows.forEach(function(el) { el.classList.toggle('collapsed', !collapsed); });
      header.classList.toggle('collapsed');
      header.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
      var icon = header.querySelector('.group-icon');
      if (icon) { icon.textContent = collapsed ? '▾' : '▸'; }
    }
    // Shared by highlightTokenType (token-type legend/bars) and
    // highlightProject (per-project legend/bars, 2026-07-21) - the two
    // never show on screen at once (only one trend-mode-tab is visible),
    // so they can safely share the one data-highlight attribute/CSS
    // instead of needing two independent highlight systems.
    // Hover only changes the cursor (CSS cursor:pointer) - it does NOT
    // touch the highlight (2026-07-21: a hover-preview version of this
    // was tried and reverted - "I want it to react only to a plain click,
    // with ctrl and shift"). Only a real click changes data-highlight.
    function toggleHighlight(cls, event) {
      var body = document.body;
      var current = body.getAttribute('data-highlight');
      var currentSet = current ? current.split(' ') : [];
      // shift = ADD this one to whatever's already selected (classic
      // multi-select semantics, 2026-07-22 - replaces an earlier
      // "select everything" behavior that always produced the same
      // result regardless of what was clicked, indistinguishable from a
      // second shift+click); ctrl/cmd = remove just this one; plain
      // click = single-select toggle.
      if (event && event.shiftKey) {
        if (currentSet.indexOf(cls) === -1) { currentSet.push(cls); }
      } else if (event && (event.ctrlKey || event.metaKey)) {
        var index = currentSet.indexOf(cls);
        if (index !== -1) { currentSet.splice(index, 1); }
      } else {
        currentSet = (currentSet.length === 1 && currentSet[0] === cls) ? [] : [cls];
      }
      if (currentSet.length === 0) {
        body.removeAttribute('data-highlight');
      } else {
        body.setAttribute('data-highlight', currentSet.join(' '));
      }
      syncPressed();
    }
    // A legend chip is "pressed" while its key is in the selection
    // (2026-10-05 review): the visual spotlight alone told a screen reader
    // nothing. Called after every change to data-highlight.
    function syncPressed() {
      var current = document.body.getAttribute('data-highlight');
      var selected = current ? current.split(' ') : [];
      document.querySelectorAll('.legend-chip[data-highlight-key]').forEach(function (chip) {
        var key = chip.getAttribute('data-highlight-key');
        chip.setAttribute('aria-pressed', selected.indexOf(key) !== -1 ? 'true' : 'false');
      });
    }
    function highlightTokenType(type, event) {
      toggleHighlight(type, event);
    }
    function highlightProject(groupClass, event) {
      toggleHighlight(groupClass, event);
    }
    // "Por modelo"'s bands reuse the group-color-N vocabulary (see the
    // comment on the spotlight rules above: only one trend pane is ever
    // visible, so the three modes can share one data-highlight). Named
    // separately from highlightProject purely so the markup says which
    // mode a band belongs to.
    function highlightModel(groupClass, event) {
      toggleHighlight(groupClass, event);
    }
    function selectPeriod(periodId) {
      document.querySelectorAll('.period-chip').forEach(function (chip) {
        var isSelected = chip.dataset.period === periodId;
        chip.classList.toggle('active', isSelected);
        chip.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        if (isSelected) { announcePeriod(chip.textContent.trim()); }
      });
      document.querySelectorAll('.period-block').forEach(function (block) {
        block.hidden = block.dataset.period !== periodId;
      });
      // Membership was decided in Python, where the span-overlap rule and
      // its tests live. This only reads the answer. Group headers use the
      // same attribute and the same class: a header whose every session
      // falls outside the period disappears with its rows, instead of
      // sitting there announcing a count for sessions nobody can see.
      // Detail panels swept alongside rows and headers (2026-08-13) - a
      // panel left open for a session outside the newly-selected period
      // must disappear with its row rather than staying on screen for a
      // row nobody can see anymore. Its `open` class (showSession /
      // closeDetail) is untouched here on purpose: switching back to a
      // period that includes the session should find the panel exactly
      // as it was left, the same way the row's own `active` class is
      // never touched by this loop either.
      document.querySelectorAll(
        '.session-row[data-periods], .group-header[data-periods], .detail-panel[data-periods]'
      ).forEach(function (el) {
        var belongs = el.dataset.periods.split(' ').indexOf(periodId) !== -1;
        el.classList.toggle('period-hidden', !belongs);
      });
      scrollTrendToNewest(periodId);
    }
    // A trend block that is `hidden` has scrollWidth 0, so scrolling it is a
    // no-op - which is why doing this once at parse time left every
    // non-default period opening on its OLDEST data. It has to run again
    // once the newly-selected block is actually visible.
    function scrollTrendToNewest(periodId) {
      document.querySelectorAll('.period-block[data-period="' + periodId + '"] .trend-scroll')
        .forEach(function (el) { el.scrollLeft = el.scrollWidth; });
    }
    function announcePeriod(name) {
      var live = document.querySelector('[aria-live][data-template]');
      if (live) { live.textContent = live.getAttribute('data-template').replace('{name}', name); }
    }
    // Enter and Space activate every role="button" / role="tab" element
    // the way a click does (2026-10-05 review: none of them was reachable
    // or operable by keyboard). preventDefault keeps Space from also
    // scrolling the page. Real <button>s are left to the browser, which
    // already does this for them.
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') { return; }
      var target = event.target;
      if (!target || target.tagName === 'BUTTON' || !target.matches) { return; }
      if (!target.matches('[role="button"], [role="tab"]')) { return; }
      event.preventDefault();
      target.click();
    });
    var initialChip = document.querySelector('.period-chip.active');
    if (initialChip) { scrollTrendToNewest(initialChip.dataset.period); }
    // Clicking outside any legend chip / chart bar clears the selection
    // back to the page's original default (everything highlighted,
    // 2026-07-22 - "clicking outside it should clear the selection and go
    // back to the default"). Captured once, before any user interaction,
    // since the set of group colors in play is only known at page-render
    // time.
    var DEFAULT_HIGHLIGHT = document.body.getAttribute('data-highlight');
    document.addEventListener('click', function(event) {
      if (event.target.closest('.legend-chip, .chart-bar')) { return; }
      if (DEFAULT_HIGHLIGHT) {
        document.body.setAttribute('data-highlight', DEFAULT_HIGHLIGHT);
      } else {
        document.body.removeAttribute('data-highlight');
      }
      syncPressed();
    });
    syncPressed();
    </script>
    """
