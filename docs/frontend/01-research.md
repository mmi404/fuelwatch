# Operator console: research

Twelve research briefs behind the design of the FuelWatch operator console. AI research agents produced them in parallel, one angle each: each searched the web, read what it could reach, and reported findings, limits and sources. The briefs are tightened here but not reinterpreted. What the project took from them is in [design decisions](02-design-decisions.md).

## How far to trust it

Every point is design reasoning from published practice. **Nothing here was tested on users**, and the claims were not independently re-checked.

| Tag | Meaning |
|---|---|
| `[read]` | The research agent fetched the page and the point comes from it |
| `[snippet]` | From a search-result snippet; the page was not opened |
| `[memory]`, `[recalled]` | The model's own knowledge, not checked against a source |
| `[judgement]` | The research agent's own recommendation |

- Points without a tag carry no stronger support than the brief's stated limits and source list.
- Web search was rate-limited for several briefs, and some pages failed to load. Each brief lists its own limits.
- Most sources are vendor documentation, design-system pages and practitioner blogs, not studies.
- The contrast ratios in brief 6 were computed by the research agent's script and not re-verified. Breakpoints and shortcut mappings are proposals.

## Where the questions were wrong

The research agents were given a loose description of the system. Read row counts and time scales in the briefs with these corrections.

| Assumed | Actual |
|---|---|
| A tick is one hour | A tick is 15 minutes (`tick_minutes`, read from the simulator) |
| About 12 stations, 3 depots, 36 station-fuel pairs | 4 stations, 2 depots, 6 routes, 3 fuels: 12 pairs |
| Stockout times can be shown over days | The estimator looks 96 ticks (24 h) ahead |

---

## 1. Industrial control-room and fuel-logistics interfaces

**Question.** How is serious control-room and tank-farm software designed, and why?

**Findings**

- **Grey is normal, colour means look here.** A healthy screen is almost all neutral grey. Colour pulls attention to an abnormal state, and overuse hides what matters. Operators call this "Gray is Good". `[read]`
- **One saturated hue family, for state only.** Amber for caution, red for critical. Never for brand, decoration or fuel type. Use shape or a letter to tell fuel types apart. `[read]`
- **Two of three channels for every state**: colour, shape, text. Example: a "P1" label inside a red square, or "RUNOUT 6h" in words. This also covers colour-blind users, about 8 percent of men. `[read]`
- **A separate alarm marker beside the object.** Do not recolour the object itself when it faults. One marker per object, showing the worst alarm only. `[read]`
- **Display hierarchy.** Level 1 is the whole network on one screen with no scrolling, Level 2 a region or depot, Level 3 the station detail. The usual failure is overloading Level 2. `[read]`
- **Few, actionable alarms.** Drop alarms with no operator action. The guide suggests about ten active alarms on screen at most. Rank them. `[read]`
- **Context, not bare numbers.** Show where a value sits in its range, against its limits, and its trend. `[read]`
- **Flat, calm geometry.** No 3D, no gradients, no decorative animation. Operators judge this style negatively at first and accept it after use. `[read]`

**Visual devices**

- Tank level: a vertical or horizontal bar with grey zones for low, normal, high; a solid fill for now; a tick for the minimum-safe level; the number beside it. Fill stays grey while normal, amber below warning, red below critical.
- Time to empty is the main number on each row, in hours, "h" always shown. A thin timeline bar per tank with a tick for the delivery ETA and a second tick for projected empty; if empty comes before the ETA, the gap is amber or red. (The delivery-ETA point comes from search-result text about a fuel-dispatch product, not a page read.) `[snippet]`
- Sparkline about 100x24 px, no axes, last 6 to 24 hours, with a dashed projection to zero. `[judgement]`
- Table-first layout sorted by time to empty, ascending; right-aligned tabular numerals; a small ranked list of proposals beside it.
- Network view as a schematic of nodes and lines, not a decorative map. Lines grey; only routes carrying an at-risk delivery get colour.
- Status strip on top: counts by priority and a data-age clock.

**Avoid**: gradient cards with big rounded KPI numbers, rainbow or pastel charts, donut charts, green for "OK" everywhere (healthy should be grey), pulsing or animated elements, 3D tanks, colour-only status, icon-heavy decoration, sidebar-plus-hero SaaS layouts, alarm floods with no ranking.

**Limits.** The ABB, automation.com and controlglobal pages returned 403 or timed out. The grey hex values and timing figures come from a blog that says they are not from the ISA-101 text. No peer-reviewed numbers were found.

**Sources.** control.com "Going Gray"; Inductive Automation Ignition high-performance HMI page; industrialmonitordirect ISA-101 guide (all `[read]`). Titan dispatcher page `[snippet]`.

---

## 2. Craft of modern operational tools

**Question.** What makes Linear, Vercel, Stripe and similar feel precise and not generic?

**Findings**

- **Type.** Geist's scale is built around small sizes: 14 default, 13 and 12 for dense UI, mono at 12, 13, 14. Adopt 12, 13, 14, 16, 20 and nothing above 24 inside the console; weights 400, 500, 600 only; mono with tabular numerals for stock, litres, IDs. `[read]`
- **Radii.** Geist uses 6 px for everyday surfaces, 12 px for menus and modals, 16 px for fullscreen. The agent suggests 4 px chips and 6 px cards. `[read]`, suggestion `[judgement]`. (The design uses 2 px everywhere: see [design decisions](02-design-decisions.md).)
- **Spacing.** 4 px base with steps 4, 8, 12, 16, 24, 32; table rows 28 to 32 px with 8 to 12 px cell padding. `[judgement]`
- **Colour scales.** A 12-step scale per hue (Radix): steps 1 to 2 backgrounds, 3 to 5 component states, 6 to 8 borders, 9 solid, 11 and 12 text. Stripe builds scales in Lab with the same lightness curve for every hue, and its rule is that two steps 5 or more apart pass 4.5:1. Linear generates each theme from three inputs (base, accent, contrast) in LCH. `[read]`
- **Neutral values (Radix gray).** Light: background `#fcfcfc`, subtle border `#d9d9d9`, stronger `#cecece`, muted text `#646464`, text `#202020`. Dark: background `#111111`, panel `#191919`, border `#3a3a3a`, stronger `#484848`, muted text `#b4b4b4`, text `#eeeeee`. `[read]`
- **Elevation.** In dark mode a surface rises by getting lighter; use 1 px borders and at most one soft shadow on floating layers. Linear explored elevation with opacities of black and white. `[read]`
- **Borders, not shadows** to separate content, and shadows only on menus and modals. `[memory]`
- **Dense but calm** comes from: neutral greys for everything and colour only for state; one border weight and one radius family; icons and labels on one baseline grid with uniform row heights; hierarchy through weight and tone instead of boxes; no cards inside cards; numbers in tabular mono, right-aligned; one accent. Linear's redesign cut accent "chrome" and raised text contrast, which made navigation denser while reducing noise. `[read]`
- **Motion**: 100 to 150 ms ease-out for hover and state changes, no bounce. Skeleton rows matching real row height; one-line empty states with one action. `[memory]`

**Limits.** Search was rate-limited. Six pages read (Linear, Vercel Geist x3, Radix, Stripe). Datadog, Grafana, Honeycomb, Sentry, incident.io and Raycast were not read.

**Sources.** linear.app/now/how-we-redesigned-the-linear-ui; vercel.com/geist/typography, /colors, /materials; radix-ui.com colors docs and light.ts/dark.ts on GitHub; stripe.com/blog/accessible-color-systems.

---

## 3. Time series, forecasts and uncertainty

**Question.** How to draw stock with a forecast and uncertainty, for one pair and for many?

**Findings**

- **One pair, detail view.** A single line chart with x in ticks. "Now" as a vertical divider. Left of it, solid actual stock; right of it, the projected median path, lighter, inside a fan. Layer a dark core band and a lighter outer band if there are more quantiles. Add a horizontal threshold line and a vertical marker where the median crosses it. Draw shipments as event marks on the axis or as a step in the path at the arrival tick. Keep y in litres from 0 to capacity. The FT Visual Vocabulary lists the fan chart as the form for projections whose uncertainty widens with distance. `[read]`
- **Many pairs.** Small multiples of sparkline-style charts, ordered by hours to stockout, worst first. Each cell holds a line, a faint band, the threshold, a dot on the last value, and the numbers beside it. Tufte: drop frames, thin lines, wide short shape. `[read]`
- **Horizon charts** were considered and set aside: they need reader training and do not carry a band cleanly. The agent could not read the source PDF. `[judgement]`
- **Draw uncertainty honestly.** Label the bands with what they mean ("50 percent of outcomes fall here"). Nest darker for more probable, lighter for less. Draw the actual P25 and P75 paths, do not symmetrise a skewed fan. Warn that interval displays are often misread as "inside or outside". No ensemble spaghetti: readers treat each line as a prediction. Show the stockout time as a range. `[read]` for the labelling and spaghetti points, `[snippet]` for nesting and skew, range display `[judgement]`
- **Axis and label rules.** Direct labels instead of a legend, two text levels, units on axes and tooltips, no rotated labels, a background-coloured outline where text overlaps lines. Annotate only what needs explaining (stockout marker, shipments, "now"). One shared y scale per fuel across stations, or percent of capacity with the unit stated; per-cell auto-scaling makes unlike series look alike. Subtle band shading, not stoplight colour. `[read]`
- **Pitfalls**: fans that look more certain than they are, per-cell auto-scaling, a y axis that does not start at 0 for volumes, overlapping shipment marks, colour-only severity, dense grids with no sort order.

**Limits.** Did not reach Observable Plot, uPlot or Our World in Data. Heer et al. PDF unreadable.

**Sources.** edwardtufte.com sparkline notebook; Financial-Times/chart-doctor visual-vocabulary; datawrapper.de text-in-data-visualizations; arxiv 2411.02576 (multiple-forecast visualisation study); cdc.gov horizon charts.

---

## 4. Network and flow views

**Question.** How to show depots, routes and stations on one screen?

**Findings**

- **Best two forms.** (1) A layered node-link diagram, depots in a column on the left and stations on the right. At this size node-link is fine; the MapTrix study found node-link degrades only for dense many-to-many flows. Edge width scales with max shipment (above roughly 25:1 between largest and smallest use min/max interpolation, and add a width legend). (2) A companion depot-by-station **matrix**, each cell showing transit hours and capacity, with closed or overloaded routes as a cell state; linked to the diagram by hover. `[read]`
- Skip Sankey, arc and geographic views: no good evidence for Sankey on this data. The metro-map (octilinear) style is overkill for a small network; borrow only straight, evenly spaced segments. `[judgement]`
- **In-transit shipments.** A marker at elapsed/transit along its route, sized by quantity, with an ETA label joined by a leader line as air-traffic data blocks do (attach the leader at the label centre so it does not jump, per EUROCONTROL). A ghost marker at the arrival end; a thin hour-tick ruler along the route; a late shipment gets an amber outline and "+Nh". `[judgement]`, leader-line point `[snippet]`
- **Disruption.** Closed road: red dashed edge with a mid-edge X, dimmed but keeping its width so lost capacity stays visible, with stuck shipments shown stopped. Depot outage: hatched node fill, red ring, "OUT" badge, faded outgoing edges. Affected station: amber ring and a "stockout in Nh" label. Never colour alone. A banner lists active disruptions and clicking an entry highlights the affected elements. `[judgement]`
- **Layout.** Fixed columns by role. Order stations by the mean row of their connected depots, break ties by id for determinism, run 2 to 3 alternating sweeps, space rows evenly. Edges as cubic Beziers with horizontal tangents, parallel edges offset deterministically. `[judgement]`, Sugiyama background `[snippet]`

**Limits.** The control-tower searches found nothing specific on flow encodings, delay colours or ETA markers: sections on in-transit and disruption are the agent's synthesis. Two PDFs returned binary.

**Sources.** en.wikipedia.org/wiki/Flow_map; ar5iv 1908.02052 (MapTrix) `[read]`. Nöllenburg metro-map survey, EUROCONTROL leader-line document, a JGAA Sugiyama paper `[snippet]`.

---

## 5. Human-in-the-loop approval

**Question.** How should a recommended action be shown for approval, with evidence, consequences, confirmation and an audit trail?

**Findings**

- **Card anatomy, top to bottom.** (1) Header: the action in one sentence, a state chip (Pending, Expiring, Approved, Executed, Rejected, Failed re-validation) and a decision-deadline countdown. (2) Key facts grid: depot to station, fuel, litres, ETA. (3) Confidence as High, Medium or Low with the number secondary, and what to do at each level; show it only if it changes the decision. (4) Why: a one-line headline plus 2 to 4 evidence chips. (5) If ignored: one consequence line with a time and a quantity. (6) Guard-rail status: pass or fail checks including the re-validation result. (7) Actions: Approve and Reject; Reject needs a reason; Edit litres and Snooze under "More". (8) Footer: proposal id, creator, link to the audit entry.
- **Why without a wall of text.** A headline plus chips, with detail one click down (Google PAIR: explain partially, use "why" in high-stakes moments). The counterfactual as a mini timeline ("runs dry in about 9 h, ETA is 6 h, window is 3 h") and an N-best alternative when confidence is low. Stripe Radar uses the same shape: a risk level in the list, "risk insights" in the detail. Terraform shows a diff with symbols and a summary line; the equivalent here is "+1 shipment, 12,000 L, 3 stations covered". `[read]`
- **Confirmation.** Prefer undo or a cancel window; use a confirmation only for serious or irreversible actions (NN/g). A dialog names the item and the consequence, with outcome-labelled buttons ("Dispatch 12,000 L", "Keep pending"), no default "Yes", no "Are you sure?". Stripe labels a refund "permanent, can't be undone": use the same wording once a shipment has left. Terraform approves the exact plan that was shown; FuelWatch's executor re-validates and refuses if the plan drifted. Escalate to a typed word or second approver only for rare, high-stakes actions. Autopilot: show guard rails as visible numbers and a one-click kill switch. `[read]` for NN/g, Terraform, Stripe; the rest `[judgement]`
- **Queue.** Sort by urgency (time to stockout); the list shows risk, key facts and age. Detail view with Previous and Next and J/K (Stripe). "Assign to me" so two operators do not act on one item. Keep a closed state with a reason, and a timeline of every action (Stripe emits review.opened and review.closed with a reason). Send only decisions needing judgement to the queue and let guard-railed autopilot handle the rest. Batch-approve similar low-risk items with per-item exclusion `[memory]`.

**Applied differently.** The FuelWatch API offers approve and reject only. Rejection takes no reason, and there is no edit, snooze or assignment, so the design draws none of them.

**Limits.** Search was rate-limited. Five pages read. Incident.io, PagerDuty and airline dispatch-release screens were **not** sourced; points from them are the agent's memory.

**Sources.** developer.hashicorp.com Terraform plan; docs.stripe.com/radar/reviews; nngroup.com confirmation-dialog; pair.withgoogle.com explainability-trust; GitHub pull-request review docs.

---

## 6. Typography and colour

**Question.** Which free fonts and which colours, in light and dark?

**Findings**

- **Type.** UI text in IBM Plex Sans (400, 500, 600), 13 px table cells (line-height 1.4), 12 px labels (uppercase, letter-spacing 0.04em, weight 500), 14 px body, 20 px/600 panel titles. Figures, IDs and timestamps in IBM Plex Mono (400, 500), 13 px in tables, 28 to 32 px for hero numbers. Tabular numerals on Plex Sans containers too. `display: swap`, CSS variables. Alternatives: Instrument Sans with JetBrains Mono; Geist. DM Sans has no tabular figures. Plex is IBM's own enterprise typeface and not an AI default. `[judgement]` on the choice, one fetched blog for the sizes and tabular-numerals point.
- **Status palette** (light on `#F6F7F8`, dark on `#0F1317`; contrast ratios computed by the agent by script, at least 4.9:1 light and 6.3:1 dark on both page and card):

| Status | Light | Dark |
|---|---|---|
| OK | `#00704A` | `#3DD6A0` |
| WATCH | `#8A6100` | `#F0C23A` |
| WARNING | `#B84A00` | `#FF9442` |
| CRITICAL | `#C41230` | `#FF6B7D` |
| UNKNOWN | `#5B6670` | `#9AA5AF` |
| OUTAGE | `#7A2A8C` | `#C98BE0` |

  Hue anchors come from Okabe-Ito (bluish green, orange, vermilion), darkened for text. Okabe-Ito is a categorical palette, so this is an adaptation. Always pair with an icon shape and the label text. Mode chips: LIVE the OK colour with a pulsing dot, DEGRADED the WARNING colour, STALE the UNKNOWN colour with a dashed outline.
- **Fuel palette**, kept off the status hues: petrol magenta `#9A3F7A` / `#E58BC0`, diesel teal `#0A7B83` / `#4FD0D8`, octane earth brown `#8A5A3C` / `#C99468`. Render as **outlined** chips with a P, D or O letter so they never read as filled status badges. Diesel teal sits closest to OK green, so the outline and letter keep them apart.
- **Neutrals** at OKLCH hue about 255, chroma 0.005 to 0.02, so there is no pure grey. Light: page `#F6F7F8`, surface `#FFFFFF`, border `oklch(90% .006 255)`, muted text `#55606A`, text `#171C21`. Dark: page `#0F1317`, surface `#161B20`, raised about `oklch(24% .012 255)`, border `oklch(32% .012 255)`, muted `#9BA6AF`, text `#E8ECEF`. Dark mode is its own set of surfaces, not an inversion: lighter as they rise, lower chroma.
- **One accent**: signal blue `#0B5FB0` on light (6.0:1), `#6BB0F5` on dark (8.1:1), used only for links, focus rings, selected rows and the primary button. Blue is neither status nor fuel. In dark mode put dark text on the `#6BB0F5` fill. The claim that Linear and Grafana use one restrained accent was not read. `[memory]`

**Applied differently.** Level OK is drawn in neutral grey, not the green in this table; green is kept for the LIVE indicator.

**Limits.** Sources are secondary blogs. IBM typeface page and a design-token ADR were search results only.

**Sources.** fontalternatives.com best-fonts-dense-dashboards; conceptviz.app Okabe-Ito reference; flaviocopes.com css-oklch-color-mix.

---

## 7. What "AI slop" is, and what to do instead

**Question.** What are the tells of generic AI-generated UI, and what distinctive alternatives fit this domain?

**Findings**

- **Ten tells.** Purple or indigo to blue gradients, gradient text and glow on near-black (likely origin: Tailwind indigo defaults). Inter or system-ui as the only typeface ("type is the fastest signal of authorship"). Glassmorphism and blurred blobs. rounded-xl or 2xl cards with soft shadows around everything, including cards inside cards. A centred hero followed by three identical icon-in-circle cards. Emoji or Lucide icons as decoration. Decorative sparklines, a donut top right, and 48 to 72 px hero-metric tiles. Uniform spacing, radius and card height so nothing has hierarchy. Gratuitous badges, one-sided coloured borders, every button primary. Generic copy and data: "Scale without limits", lorem text, "Sarah Johnson", no empty or error states. `[read]`
- **Buildable alternatives that give a point of view.**
  - Engineering drawing: title block, sheet and revision numbers, 1 px hairline rules, zero radius, no shadows, surfaces defined by borders.
  - Nautical or topographic chart: map-first layout, graticule, route symbology, a printed-chart palette (paper cream, ink black, one signal orange).
  - Railway timetable or departure board: dense tabular grid, tabular figures, mono or condensed face, rule-separated columns, for tanker schedules.
  - Typewriter or dispatch report: a mono "log" panel with timestamps and stamped status words in place of coloured pills.
  - Bank-note guilloche as one restrained SVG texture on headers or empty states, never on data.
  - Control-room logic: colour as function only, named tokens such as `alarm`, `low-stock`, `ok`; one display face plus one body face.
  - Deliberate asymmetry: a dominant map or queue with receding secondary panels.
  - Designed empty, loading and error states in operator language, with real depot names, litres and times.
- **Slop test checklist.** Cover the logo: could this belong to any other product? Any gradient, glow or blur, and if so does it carry meaning? Is every colour named by function? Two or more radii or shadow styles, or cards in cards? Do size and spacing vary on purpose with one clear focal element? Is every sparkline or number backed by real data? Are empty, loading and error states designed? Is the copy specific, with units, names and numbers? Would a banned-class lint (indigo, rounded-2xl, shadow-lg) pass? Is the first-glance read of fuel status instant, at a squint?

**Limits.** Sources are practitioner blogs and opinion, not studies. One essay returned 403.

**Sources.** world.hey.com/kostac spot-the-slop; builtin.com ai-design-slop-era; pixelslop ai-slop-patterns.md; dev.to alanwest "fix the AI-generated look".

---

## 8. Real-time health and freshness

**Question.** How should a live UI say whether its data can be trusted?

**Findings**

- **Status chip, always visible.** Each mode has a colour, a shape and a word (never colour alone, NN/g). LIVE: green dot with a slow pulse, "LIVE · 1s ago". DEGRADED: amber triangle, "DEGRADED · polling 5s", tooltip says why. STALE: red or grey hollow icon, "STALE · data as of 10:42:07 (3m ago)"; dim the numbers to about 60 percent, stop all motion, disable or confirm risky writes. STARTING: neutral, skeletons, "Connecting…". `[read]` for the principles, specifics `[judgement]`
- **Age counter** that ticks locally every second from the last event time, recoloured on thresholds (over 3 s amber, over 10 s red in the agent's example). `[judgement]`
- Retry silently with exponential backoff; show a non-sticky banner such as "Offline… Reconnecting…" only after retries fail; keep the last data on screen, never blank or reload. `[read]` (Smashing, Carbon)
- **Tick progress.** "Tick 1,482" in tabular numerals next to a thin 1 s bar that resets on each tick; if it stalls past 2 ticks escalate to DEGRADED, then STALE. Skeletons only for STARTING; never a spinner over data already on screen. `[read]`
- **Animating change.** Flash only the cells that changed, a soft pulse or background fade of 200 to 400 ms. Number tween only for headline KPIs. Reorders under 300 ms, chart transitions 300 to 600 ms. Pair each flash with a delta sign, not colour alone. Batch one tick's changes into one paint. Mark the latest sparkline point with a dot. Honour `prefers-reduced-motion` with a static "updated" marker. Do not animate on SSE reconnect snapshots; diff silently. `[read]` and `[judgement]`
- **Failed writes.** Inline, next to the control; toasts only for system-wide events; critical errors never auto-dismiss; preserve what the user entered. 401: inline on the token field, "Token rejected. Check it and retry.", do not clear the field. 409: an inline panel banner, "State changed (tick N). Review the latest and retry.", with one-click re-apply. 422: field-level message with the fix. 5xx or network: a persistent inline "Not applied. Retry", and roll back any optimistic update visibly. Optimistic UI only for low-risk actions. `[read]` (Carbon, NN/g), wording `[judgement]`
- `role="status"` with `aria-live="polite"` for mode changes.

**Limits.** Search returned mostly blogs and design-system docs; nothing first-party on Linear, Grafana, Datadog or Bloomberg.

**Sources.** smashingmagazine.com 2025/09 ux-strategies-real-time-dashboards; carbondesignsystem.com notification-pattern; nngroup.com error-message-guidelines; Carbon loading usage and Galaxus loaders (search results).

---

## 9. Accessibility, keyboard and responsive

**Question.** What do dense operator consoles most often get wrong, and what is the minimum good practice?

**Findings**

- **Shortcuts.** Ctrl/Cmd+K command palette listing every command with its shortcut, case-insensitive fuzzy search, toggles on the same key, returns focus on close (Superhuman). `?` for a searchable cheat sheet. Single keys when focus is not in an input: `/` search, `j`/`k` next and previous row, `Enter` open, `Esc` back, `g` then `o`/`s`/`l` go-to, `t` theme, `d` density. Surface them as `<kbd>` hints, a "Press ? for shortcuts" footer, and a visible palette button on phones. Do not override browser or OS shortcuts. Every shortcut has a visible UI path. The key mapping is the agent's own; the Linear and GitHub specifics were not verified. `[read]` for Superhuman, `[judgement]` for the rest
- **Rules most often broken.**
  - Colour-only status (WCAG 1.4.1, level A): pair with a text label and a distinct shape, status text at 4.5:1, icon boundaries 3:1.
  - Weak focus: WCAG 2.2 AA requires 2.4.11 Focus Not Obscured, so a sticky header must not cover a focused row; use `scroll-padding-top` equal to the header height and a 2 px, 3:1 `:focus-visible` outline with offset.
  - Small targets (2.5.8 AA): at least 24 by 24 CSS px or 24 px spacing; 32 px desktop and 44 px touch preferred.
  - Live-region spam: keep one empty polite `role="status"` in the HTML from load, throttle updates, announce a summary ("3 stations critical"); use `role="alert"` alone for a new critical event; `role="log"` for a feed; `aria-atomic="true"` for counters.
  - Grids: a plain `<table>` for static data; `role="grid"` only if cells are interactive, with one tab stop (roving tabindex), arrows between cells, Home/End and Ctrl+Home/End.
  - Reduced motion: wrap pulsing and animation in `prefers-reduced-motion: reduce`.
  - Drag-only actions (2.5.7 AA) need a non-drag alternative. `[read]`
- **Theme, density, responsive.** Tokens on `:root` with `color-scheme: light dark`, redefined under `prefers-color-scheme: dark` guarded by `:root:not([data-theme=light])` and again under `[data-theme=dark]`; set `data-theme` from localStorage in an inline `<head>` script to avoid a flash, in try/catch. `data-density` changes row height and padding tokens but never below the 24 px target. Breakpoints (the agent's own): 1024 and above, tables with a side detail panel; 640 to 1023, panel becomes a drawer and secondary columns hide; under 640, cards or stacked rows. Phone keeps the status summary, critical alerts, the primary action and the palette button; charts collapse to sparklines; no horizontal page scroll. A larger-type option and high contrast for projectors. `[judgement]`

**Limits.** Linear and GitHub shortcut specifics not verified. Breakpoint values are recommendations, not sourced.

**Sources.** w3.org WCAG 2.2 what's-new, Understanding Use of Color, ARIA APG grid pattern; MDN ARIA live regions; blog.superhuman.com command palette; web.dev prefers-color-scheme.

---

## 10. Presenting risk and countdowns

**Question.** How do triage-style UIs rank and encode risk and time-to-event without alert fatigue?

**Findings**

- **Time to stockout with uncertainty.** The median as a big figure ("~18h") with a thin P25 to P75 bar on a shared 0 to 72 h axis, the same for every row, so bar position reads as urgency. Status-band backdrops (CRITICAL under 12 h, WARNING under 24 h) so a bar that crosses a band edge reads as "could be worse". Keep the interval simple: one pair does not need a fan chart; put that in the drill-down. Print the worst-case end as text. Lead with the earliest plausible end when ranking, so uncertainty makes a pair rank higher, not lower. UNKNOWN gets a hatched grey bar with an age label, never a number. `[recalled]`, the uncertainty-survey support could not be read
- **Ranking.** Severity, then earliest-plausible hours, then shortfall litres, with a stable sort so rows do not jump. Rank by impact, not event count: a late shipment with enough cover is noise (an IBM/SCMR glossary example: 60 late of 1,100 shipments but only 9 breached days of cover). `[snippet]` for the example. Default to a flat "worst 5" on top then collapse OK rows into counts; alternatively a station-by-fuel grid where cell colour is status and text is hours, beside a ranked list. Pair every status with an icon or letter. (Written for 36 station-fuel pairs; this network has 12.)
- **Alert fatigue.** Dedupe by key; group by root cause so one depot outage covers its dependent pairs; a hold-down before firing (`for` clause) to ignore blips; severity from impact, page only CRITICAL, WATCH goes to a digest; acknowledgement with an owner and re-escalation only past a timeout; every alert expires or auto-resolves; silences are time-bounded; track alerts per day, time to acknowledge and the share that led to action. `[read]` (Atlassian, Better Stack)
- **Hero strip of a control tower.** Counts by status; the single worst pair with its countdown and range, shown big; total shortfall litres and stations affected; a freshness indicator and the UNKNOWN count; the top one to three root-cause alerts each with its blast radius; the next recommended action and its owner. A control tower should recommend an action, not only show status (IBM, LogisticsViewpoints). `[read]`

**Applied differently.** The 72 h axis and the 12 and 24 h cut-offs are this brief's proposal. FuelWatch's estimator looks 24 h ahead and has its own level rules, and the design follows those. It reports two times, from the mean and the P90 demand paths, not a median with a P25 to P75 range. The API has no alert acknowledgement.

**Limits.** Search rate-limited after the first batch; two pages returned 403 or an unreadable PDF. The NEWS2 (early-warning score), IROPS and heat-map points are recalled practice, not fetched.

**Sources.** atlassian.com 5-ways-to-reduce-alert-fatigue; betterstack.com alert-fatigue guide; ibm.com supply-chain control-towers; logisticsviewpoints.com 2026/04/23 exception management; arxiv 2011.07586 (title only).

---

## 11. Demo and storytelling

**Question.** How do winning hackathon operational tools present a live scenario in 3 to 5 minutes?

**Findings**

- **Demo flow, three minutes.** 0:00 to 0:20 hook: one stat and the live overview in a healthy state, name the question. 0:20 to 0:40 solution in one sentence, show the policy badge and the service-level figure. 0:40 to 2:30 live demo (about 110 s): pick a preset, arm, inject, the KPI dips and a narrated annotation appears, switch policy none to LP and show recovery on the same timeline, open Results. 2:30 to 2:50 tech and impact. 2:50 to 3:00 close. Have a recorded backup. Judges' first question is "does it work?" so keep the console free of code and half-built features. Use realistic names and numbers. `[read]` (one hackathon-tips blog)
- **Scenario panel controls.** Preset selector; pause, resume, reset, speed; replay scrubber; policy selector; five fault buttons with target and severity; active-fault list with per-fault clear and a global clear-all.
- **Guards** (the agent's design, not found in a source): faults disabled until Demo mode is armed, arming times out after about 60 s; two-step confirm showing target and duration; a persistent "SIMULATED" banner; a cooldown and bounded duration so faults auto-expire; a kill switch; an audit strip of every injection. Chaos-engineering practice supports limited blast radius and an abort control. `[snippet]` for the chaos source
- **Benchmark display.** Grouped bars of service level per policy per scenario with "none" as baseline; deltas in percentage points with the sign convention stated; include replicates and error bars or a confidence interval; compare paired by replicate and report the CI on the mean difference, and say a tie is a tie; show trade-offs (cost, latency, unmet demand) since a policy can win on one measure and lose on another; annotate with the seed and "same demand trace for every policy"; keep the axis at zero for bars; add a small timeline overlay marking fault onset and recovery. `[read]` (a simulation-textbook chapter on comparing scenarios)

**Applied differently.** The FuelWatch API has run, pause, step, reset and autopilot controls. It has no fault-injection endpoint, so the console draws no fault buttons. The benchmark has one seed and no faults, so no error bars are drawn.

**Limits.** Evidence is thin: most hackathon sources are blogs, not rubrics. Nothing found on fault-injection UI patterns. Grafana Play's source said nothing about guided scenarios.

**Sources.** blogs.reskilll.com hackathon demo tips 2026; eng.libretexts.org Standridge "Comparing Scenarios"; grafana.com blog Grafana Play updates; insper.github.io chaos reliability (snippet).

---

## 12. SVG charts in React without a library

**Question.** How to build many small, crisp, accessible SVG charts in Next.js 14 and React 18?

**Findings**

- **Approach.** Hand-roll the sparklines as a pure `<Sparkline points w h/>` with a fixed `viewBox` and `preserveAspectRatio="none"`, stroke with `vector-effect="non-scaling-stroke"` (MDN: baseline widely available since 2020) so a 1 px stroke stays 1 device px when stretched. `shape-rendering: geometricPrecision` for diagonals, `crispEdges` only on axes and grid. `[read]` for vector-effect, rest `[memory]`
- **Libraries.** Add **d3-shape only** (`line`, `area`, `curveMonotoneX`; `defined()` handles gaps, `.digits()` shortens path strings); d3-scale only for the large chart's nice ticks and time scale. Skip uPlot (canvas, about 50 KB, no SSR story, no per-point DOM for accessibility), Observable Plot (large, DOM-generating) and visx (a thin d3 wrapper). The d3-shape ESM and tree-shake claim was confirmed only in general terms. Measure the large chart with a `ResizeObserver`. `[read]` for d3-shape and uPlot pages, rest `[memory]`
- **Next.js pitfalls.** Hydration mismatches come from `Date()`, `window` and `localStorage` in render (Next docs): format with a fixed `Intl.DateTimeFormat('en-GB', {timeZone:'UTC'})` or after mount; put the large chart in `dynamic(..., {ssr:false})` or an `isClient` flag. Sparklines can render on the server if their input is serialised identically. `[read]`
- **Performance.** `React.memo` each sparkline, compare by a data version or last timestamp, keep each station's series reference stable between SSE ticks, `useMemo` the path, keep the hover state local so a hover does not re-render the other 35. A single `onPointerMove` on the `<svg>` computing the nearest index. CSS variables for series colour so dark mode needs no re-render. FLIP for list reordering (measure, then `transform` with the Web Animations API); `document.startViewTransition` as progressive enhancement only. `[memory]`
- **Accessibility.** The W3C two-part pattern: a short description plus a long description (the data). `role="img"` and an `aria-label` on each sparkline; the long description as a real visually-hidden `<table>` (not `display:none`); one roving-tabindex focus target with arrow keys on the large chart, the same tooltip on focus as on hover, dismiss on Escape; one throttled `aria-live="polite"` summary for the whole view. `[read]` for the W3C pattern, rest `[memory]`

**Applied differently.** The design adds no dependency, d3-shape included; paths are drawn by hand.

**Limits.** Search rate-limited after one query. Four pages read. ResizeObserver, FLIP, view transitions, memoisation, CSS variables, `shape-rendering` and the library comparison are from memory and should be checked before relying on them.

**Sources.** developer.mozilla.org vector-effect; d3js.org d3-shape line; nextjs.org hydration-error; github.com/leeoniya/uPlot; w3.org WAI images complex.
