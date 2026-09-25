# Design brief: the H1 demo site

Scope: the static GitHub Pages demo in `pages/` (`index.html`, `assets/style.css`,
`assets/app.js`, `assets/favicon.svg`), built by `pages/build.sh` and deployed by
`.github/workflows/pages.yml`. This file is not published; `build.sh` copies only
`index.html` and `assets/`.

## 1. Understand

### Product

Schrödingers Quant is a Freqtrade spot bot for Kraken that ships inert: dry-run, no
credentials, started in `stopped`. The repository holds one strategy (H1, a 20/10-day
channel breakout on BTC/EUR 4h), the research that decided whether it may run, and
the operations around it (backups, restore, health ping, systemd, runbooks).

The site is one page. It replays the recorded backtests from
`research/experiments/H1/equity-curves.json`: six runs (train / validation /
held-out × base / stress costs), each with daily marked-to-market equity for H1 and
buy-and-hold on a fixed €1,000 stake, summary metrics, and the trade list. It then
states the predeclared decision rule (K1–K4), why the evidence is weak, and how the
bot is wired.

The project's own voice is the best asset. The README opens with "The bot is both
trading and not trading until you look at the config." The current headline, "A
breakout strategy, with the drawdown left in.", is good. The copy is specific,
unhyped and honest about failure (the first held-out run failed K2 and is published).

### Moment of value

A visitor sees the held-out result against buy-and-hold, then sees that the
pass/fail rule was written before the data was run, and that H1 passed its hardest
criterion by less than one point (30.4 % drawdown against a 31.3 % limit). The
value is the combination: a result, and the reason to trust or doubt it. Today the
verdict ("GO, marginal") sits in a grey note box above the fold and the one-point
margin is the second line of the second of four identical cards.

### Audience (inferred; see open questions)

1. **Engineers and quant-curious developers arriving from GitHub**, often Freqtrade
   users. Expert readers of code and charts, intermediate in statistics. They have
   seen many trading-bot repos with up-only equity curves and "passive income"
   language, and they distrust exactly that. Quality signals for them: the drawdown
   shown, costs stated, held-out data separated, provenance (which file, which
   command), a benchmark that is allowed to win, reproducibility.
2. **Reviewers of the author's work** (peers, possibly employers) who judge rigor and
   taste in a few minutes. They skim: headline, one chart, the verdict, the diagram.
3. **The operator themself**, returning to check numbers. Wants deep links
   (`?period=…&cost=…`) and exact values, fast.

Their emotional state is sceptical and curious, not hopeful. They want to find the
catch. The design has to make the catch easy to find.

### Journeys

1. Land → understand what this is and that nothing is live → read the held-out chart
   against buy-and-hold. (primary)
2. Switch period and cost → watch H1 lag in the validation bull market and lose ~14
   points to stress costs.
3. Read the decision rule → see the margin → read the caveats → open the record.
4. Scan the system diagram → go to GitHub.
5. From the README: arrive on a deep link (`?period=validation&cost=base`).

### Current state

- Stack: hand-written HTML, CSS and ~330 lines of vanilla JS drawing SVG charts. No
  build step, no dependencies, no web fonts. Keep it that way in spirit.
- Tokens: colour tokens on `:root` with a duplicated dark block; no type, spacing,
  radius or motion scale. Values are ad hoc (12, 14, 16, 18 px paddings; font sizes
  12–46 px without a ratio).
- What works: honest copy, keyboard-readable charts with tooltips, radiogroup
  semantics, URL state, dark mode, the drawdown chart, position shading, end labels.
- What feels generic: system-ui everywhere; every block is a 14 px-radius bordered
  card; four identical metric tiles and four identical criteria cards; blue/orange
  default series colours; green pill "✓ Pass" tags; a chart-line-in-a-blue-square
  favicon that could belong to any finance app. Nothing visual connects to the
  name, to research practice, or to the project's point of view.
- Hierarchy problems: the verdict is a footnote; the four tiles give "Trades: 14"
  the same weight as net return; buy-and-hold is a separate tile instead of a
  comparison; the K2 margin, the most important number on the page, is not
  visualised; the trade list is collapsed.
- Broken or weak states: before data loads the tiles are empty and the charts have
  zero content (layout jump when tiles render); the fetch error is written into the
  tile grid as bare text; without JS the results section is blank; the position
  bands are full-height grey slabs that compete with the lines, especially in dark
  mode; on phones the legend stacks into three lines and the y-axis eats ~20 % of
  the width; the tooltip has a generic drop shadow.

### Constraints (load-bearing)

- Data contract: `data/equity-curves.json` shape as produced by
  `make research ARGS=equity-curves`. The site may not recompute or alter recorded
  metrics beyond the existing drawdown-from-daily-series chart.
- URL contract: `?period=train|validation|heldout`, `&cost=base|stress`,
  `&theme=light|dark`. README links depend on it.
- Build contract: `build.sh` copies `index.html` and `assets/` (minus
  `assets/screenshots/`). New static files must live in `assets/`. No bundler.
- README screenshots in `pages/assets/screenshots/` show the site; they must be
  regenerated after a redesign.
- Honesty: every number must come from the data file or the experiment record.
  K1–K4 values are hard-coded in HTML today and match `record.md`; keep them exact.
- Accessibility: keep radiogroup keyboard behaviour, keyboard-readable charts, the
  SVG diagram's title/desc, reduced motion. Reach WCAG 2.2 AA including series
  colours against both backgrounds and a non-colour way to tell series apart.
- Privacy/performance: the author is in Germany; loading Google Fonts from Google's
  CDN has been held to breach GDPR (LG München I, 2022). Self-host any web font as
  subset WOFF2 in `assets/`, OFL-licensed, `font-display: swap` with metric-matched
  fallbacks, at most ~120 KB total.
- English only. No analytics, no cookies.

### Open questions

1. Who is the primary audience: Freqtrade/engineering peers, or reviewers of your
   work? (I assumed engineers first, reviewers second.)
2. Is the favicon a brand mark to keep, or free to replace? It is also the README
   logo.
3. Are self-hosted web fonts acceptable (~80–120 KB), or must the site stay on
   system fonts?
4. Should the redesign regenerate the README screenshots? (Assumed yes.)
5. Should the forward paper-trading period be shown as "not yet observed" on the
   page? It is true today and becomes a place for real forward data later, but it
   adds a promise the page must keep updated.

## 2. Design direction

Three directions, each grown from something the project actually is.

### A. The experiment record

**Concept.** The page is the lab record of a preregistered experiment: hypothesis,
rules fixed in advance, the one-time held-out run, the verdict and its caveats, each
dated. Scientific preregistration is the project's real method and exactly what
this sceptical audience wants to verify. The design's job is to make *when things
were decided* and *by how much the test was passed* visible.

**Typography.** Type carries the record's two voices.
- Prose and headings: **Source Serif 4** (OFL, variable with optical sizes). Its
  display optical size is sharp and editorial at 44–56 px; its text size reads like
  a journal at 17–18 px. It signals "written argument", not "product".
- Data: **Commit Mono** (OFL), a neutral, unfashionable monospace with good tabular
  figures. Every number, date, file path, criterion ID and axis label is set in it,
  so the reader can tell measured from written at a glance.
- Two families, three weights in total (serif 400/600 variable, mono 400). Scale:
  perfect fourth (1.333) from 17 px: 13 / 17 / 22.7 / 30 / 40 / 53. Mono runs one
  step smaller than surrounding serif and slightly tracked.

**Colour.** Light: a cool, barely warm paper (`#f7f6f2`), ink `#15171a`. Roles:
- H1: **registrar blue-black ink** `#1d3f8f`, the only saturated colour, because
  H1 is the subject.
- Buy-and-hold: **graphite** `#8a8780`, dashed. The benchmark is context, not a
  competitor, so it does not get a rival hue.
- Limit / threshold: **vermilion** `#c2410c`, used only for pre-set limits (the K2
  line, the −20 % stop, the drawdown floor). Red means "a rule", never "a loss".
- Rules and grid: ink at 8–14 % opacity.
- No green. A pass is a check mark and the word "pass" in ink; the design refuses
  traffic lights because a pass here is not good news, only permission.
Dark ("reading lamp"): `#121416` ground, `#e7e4dc` ink, H1 lifted to `#8fb0ff`,
vermilion to `#ff8a5c`. All roles checked for AA against both grounds.

**Layout.** A 12-column grid at 1120 px with a fixed **margin column** (3 cols)
on the left for marginalia: dates, file paths, criterion IDs, units. The main
column (8 cols) holds prose and charts; charts break out to 11 cols. Sections are
not cards; they are separated by hairline rules and numbered like a record
(§1 Result, §2 Decision, §3 Caveats, §4 System). Density is moderate: generous
line length control (62–68 ch) for prose, tight for data. On phones the margin
column becomes a mono line above each block ("2026-09-24 · predeclared"), a
deliberate form, not a collapsed sidebar.

**Motion.** Almost none. The crosshair follows the pointer; switching run
cross-fades the chart in 160 ms (opacity only); nothing animates on scroll. Reduced
motion: no transitions at all.

**Signature details.**
1. **The margin gauge.** K2 drawn as a ruled scale: buy-and-hold drawdown 52.2 %,
   the 0.6 × limit at 31.3 % marked in vermilion, H1 at 30.4 % in ink, with the
   0.9-point gap labelled. It sits next to the verdict in §1, so "GO, marginal" is
   shown, not asserted.
2. **Dated marginalia.** "Rules frozen 2026-09-24" sits in the margin next to the
   rule; "run once" next to the held-out tab. The chronology of the method becomes
   the page's structure.
3. Position shading becomes a thin **exposure strip** under the x-axis (a ruled
   barcode of time-in-market) instead of full-height grey bands, so the lines stay
   the subject.

**Refuses.** Cards, rounded corners over 2 px, shadows, pills, green/red
performance colouring, icons, hero imagery, scroll animation, big "+39.8 %"
marketing numbers without their benchmark beside them.

### B. The closed box

**Concept.** Built on the name and the README's first line: the bot is both trading
and not trading until you look. The page is organised around **observed vs
unobserved**. Everything recorded (2020 → 2026-09-20) is drawn solid; everything not
yet observed (forward paper trading, the €10 pilot, live) is drawn as an explicit
hatched region: the chart continues to the right into an empty, labelled box. The
config state (dry-run / stopped) is shown as a two-state switch that is visibly
"closed".

**Typography.** **Schibsted Grotesk** (OFL) for headings, tight and newspaper-hard,
with **JetBrains Mono** for data. Large, confident headings; scale 1.414.

**Colour.** Near-monochrome ink on white; one "observed" colour (a dense teal
`#0f5f5c`) and a hatch pattern for unobserved. Buy-and-hold in grey.

**Layout.** Asymmetric two-part page: a wide observed half and a narrow, mostly
empty unobserved column that runs the whole height, filling in only with "not yet"
states (paper trading: 0 days; live: €0). Strong horizontal bands.

**Motion.** A single state change: the "box" hatch shifts when you switch between
recorded runs and the forward placeholder. Otherwise static.

**Signature details.** The hatched future on every chart; a "state of the box"
header that reads `dry_run: true · state: stopped` from the tracked config.

**Refuses.** Cat imagery, wave functions, quantum pseudo-science visuals, any claim
about the future.

### C. The contract note

**Concept.** A broker's printed statement: the page is a ledger. Every run is a
statement period; every trade is a line; totals are ruled off. Aimed squarely at
readers who trust tables over charts.

**Typography.** Monospace-first: **IBM Plex Mono** for everything, with **IBM Plex
Sans Condensed** for column headers. Fixed 8 px baseline.

**Colour.** White paper, black ink, one accent for debits (vermilion). Charts as
thin line art.

**Layout.** Dense, full-width tables; the trade list is the main content, expanded;
charts are small and secondary, like sparklines in statement margins.

**Motion.** None.

**Signature details.** Ruled totals with double underlines; a statement header with
period, stake, fee schedule and source file like a real contract note.

**Refuses.** Prose-led explanation, large type, anything decorative.

### Choice: A, the experiment record, with one idea from B

A fits because the product's real differentiator is method, not returns, and this
audience is looking for the catch. A makes the method (predeclared, dated, run once,
passed by 0.9 points) the visual structure, and the serif/mono split tells readers
what was argued and what was measured without a legend. It also ages well: forward
paper-trading results slot in as the next dated section.

From B I take one element: the equity chart ends with a narrow, labelled
**"not yet observed"** region after 2026-09-20, marking where forward paper trading
will go. It is the only place the name's joke appears, and it is true. (Pending
open question 5.)

**What this trades away.** B's stronger brand wit and memorability; C's density and
table-first honesty (partly recovered by showing the trade list open, in mono, with
tabular figures). A serif is also a riskier choice for a developer audience than a
grotesk; the mono data voice keeps it technical, and the serif is limited to prose
and headings.

**Anti-pattern check.** The serif-plus-mono "editorial" pairing is common in 2025–26
sites; it is justified here only because the two voices map to two kinds of content
(argument vs measurement) and are enforced strictly, never mixed within a figure.
The off-white paper ground is also a current trend; it is kept cool and close to
white, and dark mode is a real second theme, not an inversion.

## 3. As built (2026-09-25)

Answers to the open questions: audience is Freqtrade and engineering peers; the
favicon may change; self-hosted fonts are fine; show the forward region;
regenerate the README screenshots.

- **Tokens** (`assets/style.css` `:root`): colour roles (paper, ink, ink-2, muted,
  rule, H1, hold, limit, exposure), a 1.333-ratio type scale from 17 px, a 4 px
  spacing scale, a 12.5 rem margin column, 2 px radius, two motion durations.
  Dark mode repeats the roles under the media query and `[data-theme="dark"]`.
- **Fonts** (`assets/fonts/`, 92 KB total, preloaded, `font-display: swap` with
  size-adjusted local fallbacks): Source Serif 4 variable, renamed "SQ Serif"
  for the OFL Reserved Font Name, and Commit Mono. Italic dropped for weight;
  the page uses none.
- **Structure**: masthead with the tracked config state; a record layout with a
  dated margin column that folds into a mono line on tablets and phones; the
  verdict with the K2 gauge above the fold; §1 results (form-style selectors,
  H1 against buy-and-hold readout, equity chart with an exposure strip and the
  hatched "not yet observed" region on the held-out run, drawdown chart with
  the K2 limit on held-out at base costs, an open trade ledger with bars on a
  fixed ±70 % scale); §2 criteria table and caveats; §3 diagram (a separate
  vertical diagram on phones) and facts; a colophon with provenance.
- **States**: placeholders keep the layout during loading (measured CLS 0); a
  failed fetch replaces both charts with a neutral message and hides the empty
  ledger; without JavaScript the interactive block is hidden and a single
  sentence points to §2 and the record.
- **Functional changes**: none to the URL or data contracts. The trade table
  shows holding time and plain exit names (`exit_signal` → "10-day low",
  `force_exit` → "period end"); the raw value is the cell's title. Radios use a
  roving tabindex and also accept up/down arrows; Escape hides the chart
  tooltip. The chart SVG's decorative parts are `aria-hidden`; the focusable plot
  area is no longer inside an `aria-hidden` subtree.

### Unresolved

- The forward region is a promise: when paper-trading data exists, it should be
  drawn there and the label changed.
- The hard-coded §2 figures duplicate `record.md`; generating them from the
  mtm records at build time would remove the drift risk.
- The phone legend wraps to three lines on the equity chart.
