# Plan: QTO Platform Competitive UX Analysis

## Goal
Deep visual + UX analysis of 3 (+ up to 3 bonus) SaaS QTO platforms to extract design patterns
for redesigning a PyQt6 desktop QTO tool. Screenshots saved to /tmp/qto_research/. Final
findings delivered as a structured ~2000-word report in the assistant message.

---

## Tool choice
The user explicitly named mcp__Claude_in_Chrome__* tools. Those take precedence over the
BrowserAgent playwright-cli default. All navigation, screenshots, and JS evaluation will use
the Chrome MCP tools on tab 1529156680 (already open).

---

## Sites — ordered by priority

### Required (must complete)
1. stackct.com (STACK Construction Technologies)
2. togal.ai (Togal AI)
3. kreo.net (Kreo)

### Bonus (attempt after required if context allows)
4. planswift.com
5. bluebeam.com
6. assemble.estimate-construction.com

---

## Per-site page capture plan

For each site, navigate to and screenshot:

| Page | Filename pattern |
|------|-----------------|
| Homepage hero | `{site}_hero.png` |
| Features / product page | `{site}_features.png` |
| How-it-works or demo page | `{site}_demo.png` |
| Pricing page | `{site}_pricing.png` |
| Any actual product UI (in-app screenshots visible on marketing site) | `{site}_ui_detail.png` |

Total expected: ~15 screenshots for required sites, up to ~30 with bonus.

---

## Data extraction approach per site

### Visual design language
- **Colors** — Use `javascript_tool` to query `document.documentElement` computed styles or
  scan CSS custom properties (`--color-*`, `--primary`, etc.) for hex codes. Fall back to
  visual eyeballing from screenshots.
- **Typography** — Inspect `font-family` on hero headings and body copy via JS.
- **Iconography, spacing, motion** — Visual assessment from screenshots + page text.
- **Dark mode** — Check for `prefers-color-scheme` media query presence or a theme toggle.

### UX patterns
- Navigate to the features/product page and any embedded demo or "how it works" section.
- Read page text and snapshot accessibility tree for structural clues.
- Note: actual in-app UI will not be accessible without sign-up; capture whatever the
  marketing site surfaces (product screenshots, video stills, animated demos).

### Information architecture
- Snapshot the nav structure on load (topbar/sidebar/footer links).
- Read any "pricing" or "features" mega-nav sections.

### Feature lists / pricing
- Navigate explicitly to `/pricing` for each site.
- Extract top 10 features from plan cards or feature comparison tables.
- Note gated content (demo-required, no public pricing).

---

## Handling gated content
Some sites (especially Assemble, possibly Kreo enterprise tier) may require login or demo
booking to see the actual product UI. Plan:
- Capture as much as possible from the public marketing site.
- Note explicitly in the report: "Gated — no live product UI visible."
- Do NOT attempt to create accounts or enter credentials.

---

## Report structure (delivered in assistant message)

```
EXECUTIVE SUMMARY (3-5 recurring cross-platform themes)

SITE 1: STACK (stackct.com)
  1. Visual design language
  2. QTO-specific UX patterns
  3. Information architecture
  4. Pricing / feature list

SITE 2: TOGAL AI (togal.ai)
  [same structure]

SITE 3: KREO (kreo.net)
  [same structure]

BONUS SITES (abbreviated, if visited)
  planswift / bluebeam / assemble

DESIGN VOCABULARY TABLE
  Pattern | Which sites | Recommendation for PyQt6 tool

FEATURE WISHLIST
  Top features distilled from all sites

SCREENSHOTS INVENTORY
  Filename | one-line description
```

---

## Sequencing of steps

1. Create /tmp/qto_research/ directory (already done).
2. For each required site (Stack → Togal → Kreo):
   a. Navigate to homepage, screenshot hero.
   b. Extract color/font data via JS.
   c. Navigate to features/product page, screenshot.
   d. Navigate to demo/how-it-works page if present, screenshot.
   e. Navigate to pricing page, screenshot + read feature list.
   f. Navigate back to any embedded product UI shots, screenshot.
3. Repeat abbreviated flow for bonus sites if context budget allows.
4. Compose and deliver the full ~2000-word report as the final assistant message.

---

## Notes / risks
- Cookie/GDPR banners: decline (privacy-preserving default per system rules).
- CAPTCHA: report as limitation, do not attempt to bypass.
- Video-only demos (no static product UI): note in report.
- Tab reuse: all browsing will reuse the existing tab 1529156680 (navigate, don't open new tabs
  unless content loads in a new one automatically).
- Screenshots stored at /tmp/qto_research/ with descriptive names per the pattern above.
- Final deliverable is text in the assistant message — no separate .md analysis file will be
  created per BrowserAgent rules.
