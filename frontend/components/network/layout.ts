import type { Depot, Level, Risk, Route, SimAllocation, SimEvent, Station, World } from '@/lib/api';
import { tickClock } from '@/lib/format';
import { levelRank, pairKey, rankRisks, routeClosed } from '@/lib/risk';

/** Deterministic layered layout: depots left, stations right. Pure functions, no React. */

export interface Box { id: string; x: number; y: number; w: number; h: number }
export interface Edge {
  route: Route;
  x0: number; y0: number; x1: number; y1: number;
  path: string;
  width: number;
}
export interface Slot { cx: number; cy: number; w: number; h: number }
export interface Layout {
  width: number;
  height: number;
  narrow: boolean;
  depots: (Box & { depot: Depot })[];
  stations: (Box & { station: Station })[];
  edges: Edge[];
  /** x range of the edge zone between the two columns. */
  zone: { x0: number; x1: number };
  rulerY: number;
}

const TOP = 8;
const GAP = 12;
/** Room at both sides for node strokes and the outage ring. */
const INSET = 4;
export const DEPOT_H = 96;
export const STATION_H = 72;

/** "Mirpur Fuel Station" -> "Mirpur"; "Gazipur Depot" -> "Gazipur". */
export const shortName = (name: string) => name.replace(/\s+(Fuel |Industrial |Highway |Regional )?(Station|Depot)$/, '');

/** Rough text width in user units for the sans face. */
export const textW = (s: string, px = 12, k = 0.55) => s.length * px * k;

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Infinity);

/** Barycentre ordering: sort by the mean row of connected nodes in the other layer, ties by id. */
function order(ids: string[], rows: (id: string) => number[]): string[] {
  const key = new Map(ids.map((id, i) => [id, mean(rows(id))]));
  return [...ids].sort((a, b) => {
    const ka = key.get(a) ?? Infinity;
    const kb = key.get(b) ?? Infinity;
    if (ka === kb) return a.localeCompare(b);
    return ka - kb;
  });
}

export function bezier(e: Pick<Edge, 'x0' | 'y0' | 'x1' | 'y1'>, t: number): { x: number; y: number } {
  const dx = (e.x1 - e.x0) * 0.5;
  const p = [
    [e.x0, e.y0],
    [e.x0 + dx, e.y0],
    [e.x1 - dx, e.y1],
    [e.x1, e.y1],
  ];
  const u = 1 - t;
  const c = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
  return { x: c[0] * p[0][0] + c[1] * p[1][0] + c[2] * p[2][0] + c[3] * p[3][0], y: c[0] * p[0][1] + c[1] * p[1][1] + c[2] * p[2][1] + c[3] * p[3][1] };
}

export function computeLayout(world: World, width: number): Layout {
  const narrow = width < 520;
  const depotW = narrow ? 100 : 176;
  const stationW = narrow ? 144 : 216;
  const { depots, stations, routes } = world;

  const dIds = depots.map((d) => d.id).sort();
  const sIds = stations.map((s) => s.id).sort();
  const stationsOf = (d: string) => routes.filter((r) => r.source_depot_id === d).map((r) => r.destination_station_id);
  const depotsOf = (s: string) => routes.filter((r) => r.destination_station_id === s).map((r) => r.source_depot_id);

  let dOrder = dIds;
  let sOrder = sIds;
  for (let sweep = 0; sweep < 2; sweep++) {
    const dRow = new Map(dOrder.map((id, i) => [id, i]));
    sOrder = order(sOrder, (s) => depotsOf(s).map((d) => dRow.get(d) ?? 0));
    const sRow = new Map(sOrder.map((id, i) => [id, i]));
    dOrder = order(dOrder, (d) => stationsOf(d).map((s) => sRow.get(s) ?? 0));
  }
  {
    const dRow = new Map(dOrder.map((id, i) => [id, i]));
    sOrder = order(sOrder, (s) => depotsOf(s).map((d) => dRow.get(d) ?? 0));
  }

  const slot = STATION_H + GAP;
  const stationsRegion = Math.max(0, sOrder.length * slot - GAP);
  const depotsMin = Math.max(0, dOrder.length * (DEPOT_H + GAP) - GAP);
  const region = Math.max(stationsRegion, depotsMin);
  const sOffset = (region - stationsRegion) / 2;

  const stationBoxes = sOrder.map((id, i) => {
    const station = stations.find((s) => s.id === id)!;
    return { id, station, x: width - stationW - INSET, y: TOP + sOffset + i * slot, w: stationW, h: STATION_H };
  });
  const sCy = new Map(stationBoxes.map((b) => [b.id, b.y + b.h / 2]));

  // depots at the barycentre of their stations, then pushed apart where they collide
  const desired = dOrder.map((id) => {
    const cys = stationsOf(id).map((s) => sCy.get(s) ?? TOP + region / 2);
    return { id, cy: cys.length ? mean(cys) : TOP + region / 2 };
  });
  const placed: number[] = [];
  desired.forEach((d, i) => {
    const y = d.cy - DEPOT_H / 2;
    placed.push(i === 0 ? Math.max(TOP, y) : Math.max(y, placed[i - 1] + DEPOT_H + GAP));
  });
  const bottom = TOP + region - DEPOT_H;
  for (let i = placed.length - 1; i >= 0; i--) {
    const limit = i === placed.length - 1 ? bottom : placed[i + 1] - DEPOT_H - GAP;
    placed[i] = Math.min(placed[i], limit);
  }
  const depotBoxes = desired.map((d, i) => {
    const depot = depots.find((x) => x.id === d.id)!;
    return { id: d.id, depot, x: INSET, y: placed[i], w: depotW, h: DEPOT_H };
  });

  // ports: spread each node's edges evenly along its side, ordered by the far end so lines do not cross needlessly
  const dPort = new Map<string, number>();
  const sPort = new Map<string, number>();
  const dBox = new Map(depotBoxes.map((b) => [b.id, b]));
  const sBox = new Map(stationBoxes.map((b) => [b.id, b]));
  for (const b of depotBoxes) {
    const rs = routes.filter((r) => r.source_depot_id === b.id).sort((p, q) => sCy.get(p.destination_station_id)! - sCy.get(q.destination_station_id)! || p.id.localeCompare(q.id));
    rs.forEach((r, i) => dPort.set(r.id, b.y + (b.h * (i + 1)) / (rs.length + 1)));
  }
  for (const b of stationBoxes) {
    const rs = routes.filter((r) => r.destination_station_id === b.id).sort((p, q) => dBox.get(p.source_depot_id)!.y - dBox.get(q.source_depot_id)!.y || p.id.localeCompare(q.id));
    rs.forEach((r, i) => sPort.set(r.id, b.y + (b.h * (i + 1)) / (rs.length + 1)));
  }

  const maxShip = Math.max(1, ...routes.map((r) => r.max_shipment));
  const x0 = INSET + depotW;
  const x1 = width - stationW - INSET;
  const edges: Edge[] = routes
    .filter((r) => dBox.has(r.source_depot_id) && sBox.has(r.destination_station_id))
    .map((route) => {
      const y0 = dPort.get(route.id)!;
      const y1 = sPort.get(route.id)!;
      const dx = (x1 - x0) * 0.5;
      return {
        route,
        x0, y0, x1, y1,
        path: `M${x0},${y0} C${x0 + dx},${y0} ${x1 - dx},${y1} ${x1},${y1}`,
        width: 1.5 + 4.5 * (route.max_shipment / maxShip),
      };
    });

  const rulerY = TOP + region + 18;
  return { width, height: rulerY + 22, narrow, depots: depotBoxes, stations: stationBoxes, edges, zone: { x0, x1 }, rulerY };
}

/** Collision-aware label slots along an edge. `taken` is mutated. */
export function placeLabel(e: Edge, w: number, h: number, taken: Slot[], ts = [0.5, 0.36, 0.64, 0.24, 0.76]): { x: number; y: number; t: number } | null {
  for (const t of ts) {
    const p = bezier(e, t);
    const s: Slot = { cx: p.x, cy: p.y, w: w + 4, h: h + 2 };
    const clash = taken.some((o) => Math.abs(o.cx - s.cx) < (o.w + s.w) / 2 && Math.abs(o.cy - s.cy) < (o.h + s.h) / 2);
    if (!clash) {
      taken.push(s);
      return { x: p.x, y: p.y, t };
    }
  }
  return null;
}

/* ---- domain helpers ---- */

export interface Shipment {
  id: number;
  route: Route;
  fuel: string;
  quantity: number;
  /** 0 at the depot, 1 at the station. */
  t: number;
  remaining: number;
  arrivalTick: number | null;
  pending: boolean;
}

const LIVE = new Set(['PENDING', 'IN_TRANSIT']);

export function shipmentsInFlight(allocs: SimAllocation[], routes: Route[], tick: number): Shipment[] {
  const byId = new Map(routes.map((r) => [r.id, r]));
  const out: Shipment[] = [];
  for (const a of allocs) {
    const route = byId.get(a.route_id);
    if (!route || !LIVE.has(a.status.toUpperCase())) continue;
    const pending = a.status.toUpperCase() === 'PENDING' || a.departure_tick == null;
    const total = a.departure_tick != null && a.expected_arrival_tick != null ? Math.max(1, a.expected_arrival_tick - a.departure_tick) : Math.max(1, route.transit_ticks);
    const elapsed = pending ? 0 : tick - (a.departure_tick ?? tick);
    const remaining = a.expected_arrival_tick != null ? a.expected_arrival_tick - tick : Math.max(0, total - elapsed);
    out.push({ id: a.id, route, fuel: a.fuel_type, quantity: a.quantity, t: Math.min(1, Math.max(0, elapsed / total)), remaining, arrivalTick: a.expected_arrival_tick, pending });
  }
  return out.sort((a, b) => a.id - b.id);
}

export interface StationView { worst: Risk | null; byFuel: Map<string, Risk>; affected: boolean }

export function stationViews(world: World, risks: Risk[]): Map<string, StationView> {
  const out = new Map<string, StationView>();
  for (const s of world.stations) {
    const mine = risks.filter((r) => r.station_id === s.id);
    const inbound = world.routes.filter((r) => r.destination_station_id === s.id);
    const usable = inbound.filter((r) => r.status === 'AVAILABLE' && world.depots.find((d) => d.id === r.source_depot_id)?.status === 'OPEN');
    out.set(s.id, {
      worst: rankRisks(mine)[0] ?? null,
      byFuel: new Map(mine.map((r) => [r.fuel_type, r])),
      affected: s.status !== 'OPEN' || usable.length < inbound.length,
    });
  }
  return out;
}

/** Only ACTIVE events: a SCHEDULED one has changed nothing yet, even at its start tick, and a RESOLVED one is over. */
export const activeEvents = (events: SimEvent[]) => events.filter((e) => e.status === 'ACTIVE');

const andList = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
const idsIn = (e: SimEvent, key: string): string[] => {
  const v = e.parameters?.[key];
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
};

interface Closable { id: string; name: string; status: string; down: boolean }

/**
 * One line per physical disruption. An active event and the status it causes (a route, depot or station that is not
 * available or open) are one line, however many nodes the event covers. A closure that no active event names, and an
 * active event whose node does not show the change, are each still listed.
 */
export function disruptionList(world: World, nameOf: (id: string) => string): string[] {
  const { tick, tick_minutes: tm, sim_time: simTime } = world.instance;
  const active = activeEvents(world.events);
  const until = (e: SimEvent) => `until ${tickClock(simTime, tick, e.end_tick + 1, tm)}`;
  const eventName = (e: SimEvent) => e.type.replace(/_/g, ' ').toLowerCase();

  // The event types that change a status, the parameter naming what they change, and how a changed node reads.
  const kinds: { type: string; param: string; nodes: Closable[]; downText: (down: Closable[]) => string }[] = [
    {
      type: 'route_disruption',
      param: 'route_ids',
      nodes: world.routes.map((r) => ({ id: r.id, name: `${nameOf(r.source_depot_id)} to ${nameOf(r.destination_station_id)}`, status: r.status, down: routeClosed(r.status) })),
      downText: (d) => `${andList(d.map((n) => n.name))} ${d.length === 1 ? 'route' : 'routes'} closed`,
    },
    {
      type: 'depot_constraint',
      param: 'depot_ids',
      nodes: world.depots.map((d) => ({ id: d.id, name: nameOf(d.id), status: d.status, down: d.status !== 'OPEN' })),
      downText: (d) => d.map((n) => `${n.name} depot ${n.status.toLowerCase()}`).join(', '),
    },
    {
      type: 'station_outage',
      param: 'station_ids',
      nodes: world.stations.map((s) => ({ id: s.id, name: nameOf(s.id), status: s.status, down: s.status !== 'OPEN' })),
      downText: (d) => `${andList(d.map((n) => n.name))} ${d.length === 1 ? 'station outage' : 'stations in outage'}`,
    },
  ];

  const out: string[] = [];
  for (const { type, param, nodes, downText } of kinds) {
    const listed = new Set<string>();
    // Events that name their targets go first, then the later end, so a node two events cover shows when the last one ends.
    const mine = active
      .filter((e) => e.type === type)
      .sort((a, b) => Number(!idsIn(a, param).length) - Number(!idsIn(b, param).length) || b.end_tick - a.end_tick || a.id - b.id);
    for (const e of mine) {
      const ids = idsIn(e, param);
      // an event that names nothing accounts for whatever of its kind is down
      const covered = nodes.filter((n) => (ids.length ? ids.includes(n.id) : n.down));
      if (!covered.length) {
        out.push(`${eventName(e)} event ${until(e)}`);
        continue;
      }
      const fresh = covered.filter((n) => !listed.has(n.id));
      if (!fresh.length) continue;
      fresh.forEach((n) => listed.add(n.id));
      const down = fresh.filter((n) => n.down);
      const unchanged = fresh.filter((n) => !n.down);
      const still = unchanged.length ? `${andList(unchanged.map((n) => n.name))} (still shown as ${unchanged[0].status.toLowerCase()})` : '';
      if (!down.length) out.push(`${eventName(e)} ${until(e)} on ${still}`);
      else out.push(`${downText(down)} ${until(e)}${still ? `, same event also covers ${still}` : ''}`);
    }
    for (const n of nodes) if (n.down && !listed.has(n.id)) out.push(downText([n]));
  }
  for (const e of active) if (!kinds.some((k) => k.type === e.type)) out.push(`${eventName(e)} event ${until(e)}`);
  return out;
}

/** Level that colours an edge: worst at-risk pair that a shipment on the route is heading to, else null (grey). */
export function edgeLevel(route: Route, ships: Shipment[], risks: Risk[]): Level | null {
  let worst: Level | null = null;
  for (const s of ships) {
    if (s.route.id !== route.id) continue;
    const r = risks.find((x) => pairKey(x.station_id, x.fuel_type) === pairKey(route.destination_station_id, s.fuel));
    if (!r || levelRank(r.level) < levelRank('WATCH') || r.level === 'UNKNOWN') continue;
    if (!worst || levelRank(r.level) > levelRank(worst)) worst = r.level;
  }
  return worst;
}

/** "0.5 h", "1 h", "0.25 h". */
export const fmtH = (h: number) => `${Number(h.toFixed(2))} h`;

/**
 * Time to stockout. The backend looks 24 h ahead (96 ticks) and sends null when stock lasts past that.
 * `hours_p50` follows the mean demand path ("expected"), `hours_p90` the P90 path ("cautious"); levels come from the backend.
 */
export const timeText = (h: number | null) =>
  h == null ? 'more than 24 h' : h <= 0 ? 'now' : h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : `${h < 10 ? h.toFixed(1) : Math.round(h)} h`;

export const outlook = (r: Pick<Risk, 'hours_p50' | 'hours_p90'>) => `time to stockout: expected ${timeText(r.hours_p50)}, cautious ${timeText(r.hours_p90)}`;

/** The status line of a station node: always a level word (or "Outage"), then the expected time to stockout. */
export function stationLine(v: StationView | undefined, down: boolean, narrow: boolean, word: string): string {
  const worst = v?.worst;
  if (!worst) return down ? 'Outage' : 'No forecast';
  const head = down ? 'Outage' : word;
  if (worst.level === 'UNKNOWN') return down ? head : 'No forecast';
  if (worst.inventory <= 0) return `${head} · empty`;
  const { hours_p50: p50, hours_p90: p90 } = worst;
  if (p50 == null) {
    if (p90 != null) return narrow ? `${head} · >24 h` : `${head} · cautious ${timeText(p90)}`;
    return narrow ? `${head} · >24 h` : `${head} · more than 24 h`;
  }
  if (narrow) return `${head} · ${timeText(p50)}`;
  if (p50 <= 0) return `${head} · stockout now`;
  return v?.affected ? `${head} · stockout in ${timeText(p50)}` : `${head} · expected ${timeText(p50)}`;
}
