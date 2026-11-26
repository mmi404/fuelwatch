# Operator console: design decisions

Each decision, with the reason for it. "Brief" refers to a numbered section of the [research](01-research.md). How the decisions combine into screens is in the [console design](03-console-design.md).

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | A healthy pair (level OK) is drawn in neutral ink with a check shape. Green exists only for the LIVE indicator, so an all-OK screen has almost no hue. | Control-room practice keeps the normal state grey, so any colour means "look here" (brief 1). |
| D2 | Every status has a shape and a word: OK check-circle, WATCH ring-dot, WARNING triangle, CRITICAL octagon, UNKNOWN dashed ring, OUTAGE slashed circle. | Colour alone fails WCAG 1.4.1 (briefs 1, 9). |
| D3 | Fuel types are outlined letter tiles (P, D, O) in their own hues, never filled badges. | A fuel must not read as a status (briefs 1, 6). |
| D4 | One accent, a signal blue, used for links, focus, selection and the primary button only. | Keeps status colour meaningful (briefs 2, 6). |
| D5 | IBM Plex Sans for text, IBM Plex Mono for figures, tabular numerals. | Dense tables need aligned figures; a matching pair that is not a generic default (briefs 2, 6, 7). |
| D6 | Cool, low-chroma neutrals. Dark mode has its own surfaces, which get lighter as they rise. | Dark mode is not an inversion (briefs 2, 6). |
| D7 | Hairlines separate content. Shadows appear only on floating layers. One 2 px corner radius. | Calm density, and no generic card look (briefs 2, 7). |
| D8 | **Cover bar:** every pair is drawn on the same 0 to 24 h axis. The bar runs from the cautious stockout time to the expected one, with reference lines at 4 h, 12 h and 24 h. The status shown is the backend's. A pair with no forecast gets a hatched bar and "no data", never a number. | Position on a shared axis reads as urgency (briefs 3, 10). 24 h is how far the estimator looks. |
| D9 | Rank by severity, then by cautious stockout time, then by stock as a share of capacity, with a stable final tie-break. | Ranking on the cautious end means uncertainty raises a pair rather than hiding it (brief 10). |
| D10 | The pair detail chart shows observed stock as a solid line and projected stock as a lighter line inside one band, with a "now" divider, a zero line, capacity and shipment arrival marks. | A fan for uncertainty that widens with time (brief 3). The caption states what the band is. |
| D11 | The network view is a layered node-link diagram, depots on the left and stations on the right, with a depot-by-station matrix as its text equivalent. Routes stay grey unless they carry a delivery to an at-risk pair or are disrupted. | At this size node-link is readable, and the matrix gives the exact figures (brief 4). |
| D12 | A proposal card reads in a fixed order: the action as one sentence, key facts, a note of when the plan was made, confidence as a word, why (with evidence), what happens if ignored, alternatives, then actions. Button labels name the outcome. Errors appear inline and stay until dealt with. | An operator should be able to approve from the card, and see why (briefs 5, 8). |
| D13 | Alerts are grouped by cause with the number of pairs affected, and the list is capped with a "show all". | Many symptoms of one disruption should read as one problem (brief 10). |
| D14 | A status chip is always visible, with a shape, a word and a data age that ticks locally. Stale data is dimmed and stops moving. No spinner covers data already on screen. Only changed values flash. | The operator must know whether to trust the screen (brief 8). |
| D15 | A command palette, a shortcut sheet and single-key navigation. Every shortcut also has a visible control. | Speed for regular users without hiding anything from new ones (brief 9). |
| D16 | Charts are hand-drawn SVG with no chart library. Each has a text alternative. | The data is small, and the marks need to match the rest of the console (brief 12). |
| D17 | Numbers and times are formatted with a fixed locale. | Server and browser must render the same markup (brief 12). |
| D18 | The summary strip has **one** large figure, the worst pair's countdown, with small supporting counts. | One focal point instead of a row of equal metric tiles (briefs 7, 10). |
| D19 | The results view leads with **unmet litres** on bars that start at zero, prints the service level beside each, and labels ties as ties. | Service levels sit close to 1.0, so bars from zero would hide the differences (brief 11). |
| D20 | The visual idiom is a dispatch drawing sheet: a title-block header, numbered page headings, stamped status words and a monospace audit log. | Gives the product its own point of view (brief 7). |

## Where two briefs disagreed

| Conflict | Resolution | Reason |
|---|---|---|
| Brief 6 gives OK a green; brief 1 says healthy should be grey. | Grey with a check shape; green only on the LIVE indicator. | It makes a stressed screen stand out. |
| Brief 2 suggests 4 px and 6 px corners; brief 7 suggests none. | 2 px everywhere. | One radius, close to the drawing-sheet idiom. |
| Brief 10 asks for a summary strip; brief 7 lists large metric tiles as a generic tell. | One large figure, no tile row. | A single dominant figure with a job is not a grid of equal cards. |
| Brief 1 wants colour only for abnormal states from one hue family; the system has five levels and an outage state. | Status colours are reserved for status, and OK is neutral. | Shape and word carry the meaning without colour. |
| Brief 6 proposes IBM Plex; brief 2 refers to other typefaces. | IBM Plex. | Freely available, has tabular figures and a matching monospace. |

## What the system does not provide

The design shows only what the FuelWatch API can support.

| A brief assumed | What is true | Design response |
|---|---|---|
| A multi-day time-to-stockout scale | The estimator looks 96 ticks (24 h) ahead. A `null` time means no stockout within that window. | The axis ends at 24 h, and the label is "more than 24 h". |
| A median with a P25 to P75 range | The API gives two times: `hours_p50` follows the mean demand path and `hours_p90` the P90 demand path. | They are labelled "expected" and "cautious". No percentile range is claimed. |
| Stock quantiles for a layered fan | `/api/forecast` gives per-tick **demand** (P10, P50, P90). | Stock paths are built by adding up demand, giving one band. This assumes every tick is high (or low) together, so the band is wider than the likely range. The caption says so. |
| Known future supply to project with | Only shipments on the road have a known arrival at a station. | The projection includes those and invents no others. |
| A stored stock history | There is no history endpoint. | The console records one point per tick from when the page loads, says so, and shows a "collecting" state until there are enough points. |
| Alert acknowledgement | There is no such endpoint. | Hiding an alert applies to that browser session only, and is labelled that way. |
| Edit, snooze, assign, or reject with a reason | None exist. | Approve and Reject only, plus the override for stale data that the API supports. |
| Fault-injection buttons | The API offers run, pause, step, reset and the autopilot. | Only those controls are drawn. Disruptions are read from the snapshot. |
| Replicated benchmark runs with confidence intervals | The benchmark uses one simulator seed and injects no faults. | The results view states the limit and draws no error bars. |

## Differences from the engineering design

[ADR-10](../design/09-decision-log.md) chose Next.js with Tailwind, polling `/api/snapshot` and `/api/proposals` every second. The console design keeps Next.js and changes two things:

- **Styling:** plain CSS with design tokens and CSS Modules instead of Tailwind, because the look depends on a small set of shared tokens and hand-drawn charts.
- **Refresh:** the server-sent event stream signals that something changed and triggers a refetch, and a 5 second timer covers a stream that is down. REST stays the source of truth.
