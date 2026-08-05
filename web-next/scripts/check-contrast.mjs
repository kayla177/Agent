#!/usr/bin/env node
// Contrast guard for the surface ramp.
//
// The theme moved from pure black to charcoal, which LOWERS every text contrast
// ratio at once — the background got closer to the text, not further from it.
// Measured before the move, --muted inside the modal sat at 5.87:1; on charcoal
// the same token gives 4.68:1, which passes WCAG AA (4.5:1) by 0.18 in a modal
// that is mostly 12.8px muted text. That is not a margin, it is a coincidence.
//
// FLOOR is 5.0, NOT 4.5. AA is 4.5; the extra 0.5 is deliberate headroom so the
// next token nudge has to be a decision instead of an accident. Do not "correct"
// this to 4.5 — it is a project policy, not a standard.
//
// There is no JS test framework in this project (adding one is out of scope), so
// this follows the plain-node precedent of check-schema-drift.mjs.
//
// Run: npm run check:contrast
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const FLOOR = 5.0;
const CSS = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");

// The ramp, mirrored from globals.css. Every value is asserted to appear in the
// stylesheet below, so this file cannot silently drift from it.
const TOKEN = {
  "--bg": "#14171d",
  "--panel": "#1c2027",
  "--panel-2": "#22262f",
  "--muted": "#98a0b2",
  "--text": "#eef1f6",
};
const MODAL_BG = "#252a33";
const MODAL_BORDER = "#3b4250";
// The selected apply-path card: color-mix(in srgb, var(--accent) 6%, var(--panel-2))
// composited for the worst of the five planets (saturn — its pale accent lightens
// --panel-2 the least in luminance terms of any of the five, of the palette this
// theme ships). Hardcoded because color-mix output cannot be computed from CSS
// source text the way the other tokens above can; verified by hand against
// scripts/check-contrast.mjs's own luminance math before being pasted here.
const SELECTED_CARD_SATURN = "#2d3036";
// button.link's colour, verified against every surface it can render on.
const LINK_COLOR = "#e88a5e";

function luminance(hex) {
  const h = hex.replace("#", "");
  const parts = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const [r, g, b] = parts.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

let passed = 0;
let failed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
  } catch (err) {
    failed += 1;
    console.error(`✗ ${name}\n  ${err.message}`);
  }
}

// 1. The stylesheet really holds these values.
for (const [token, value] of Object.entries(TOKEN)) {
  check(`${token} is ${value} in globals.css`, () => {
    assert.ok(
      CSS.includes(`${token}: ${value}`),
      `globals.css does not declare "${token}: ${value}" — this script has drifted from the theme`,
    );
  });
}
check(`.modal background is ${MODAL_BG}`, () => {
  assert.ok(CSS.includes(MODAL_BG), `globals.css does not mention ${MODAL_BG}`);
});
check(`.modal border is ${MODAL_BORDER}`, () => {
  assert.ok(CSS.includes(MODAL_BORDER), `globals.css does not mention ${MODAL_BORDER}`);
});
check(`button.link is ${LINK_COLOR}`, () => {
  assert.ok(CSS.includes(LINK_COLOR), `globals.css does not mention ${LINK_COLOR}`);
});

// 2. The body honours the token instead of hardcoding a colour. --bg was dead
//    weight before this: body set `background-color: #000` directly, so changing
//    the token changed nothing at all.
check("body background uses var(--bg), not a literal", () => {
  const body = CSS.slice(CSS.indexOf("\nbody {"), CSS.indexOf("\na {"));
  assert.ok(body.includes("var(--bg)"), "body must read --bg");
  assert.ok(!body.includes("#000"), "body must not hardcode black");
});

// 3. Every text-on-surface pair clears the floor.
const PAIRS = [
  ["--muted on page", TOKEN["--muted"], TOKEN["--bg"]],
  ["--muted on panel", TOKEN["--muted"], TOKEN["--panel"]],
  ["--muted on panel-2", TOKEN["--muted"], TOKEN["--panel-2"]],
  ["--muted in modal", TOKEN["--muted"], MODAL_BG],
  ["--text on page", TOKEN["--text"], TOKEN["--bg"]],
  ["--text in modal", TOKEN["--text"], MODAL_BG],
  // .apply-path.on's tint is translucent (color-mix(…, var(--panel-2))), so it
  // composites over --panel-2, not over the modal background — verify the
  // composited surface itself clears the floor, worst planet included.
  ["--muted on selected apply-path (saturn, worst planet)", TOKEN["--muted"], SELECTED_CARD_SATURN],
  ["button.link on modal bg", LINK_COLOR, MODAL_BG],
];
for (const [name, fg, bg] of PAIRS) {
  check(`${name} >= ${FLOOR}:1`, () => {
    const r = ratio(fg, bg);
    assert.ok(
      r >= FLOOR,
      `${name} is ${r.toFixed(2)}:1, below the ${FLOOR}:1 floor (${fg} on ${bg})`,
    );
  });
}

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
