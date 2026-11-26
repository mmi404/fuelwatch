// Pure maths for the pair detail chart: what is on the road, and the projected stock path with its cautious band.
import type { ForecastRow, Route, SimAllocation } from '@/lib/api';

export const PAST_TICKS = 24;
export const FUTURE_TICKS = 48;
/** The cover target is this many ticks of expected demand. */
export const COVER_TICKS = 24;

export interface Inbound {
  id: number;
  /** Arrival tick, never earlier than now. */
  tick: number;
  liters: number;
  /** False while the shipment is PENDING: it has not left the depot. */
  departed: boolean;
  /** True when the simulator has given no arrival tick yet, so it is created tick plus route transit. */
  estimated: boolean;
}

export interface ProjPoint {
  tick: number;
  /** Stock if every tick runs at expected demand. The forecast's per-tick `p50` is the mean the backend's hours_p50 follows. */
  mean: number;
  /** Cautious lower edge: demand at its P90 in every tick. */
  low: number;
  /** Cautious upper edge: demand at its P10 in every tick. */
  high: number;
}

export interface Projection {
  /** One point per tick from `now` (index 0) forward. */
  points: ProjPoint[];
  /** First tick whose expected demand cannot be fully served, with the share of that tick the stock still covers. Null if none in the window. */
  empty: { tick: number; share: number } | null;
  /** Litres of 24 ticks of expected demand. */
  coverTarget: number;
}

const ON_THE_ROAD = new Set(['PENDING', 'IN_TRANSIT']);

/**
 * Shipments already committed to this station and fuel. Same arrival rule as the backend (stockout.py, inbound_by_tick):
 * the simulator's expected arrival, else created tick plus route transit (not departed yet), never before now.
 * Scheduled depot supply is not a shipment to a station, so nothing else is added.
 */
export function inboundFor(allocations: SimAllocation[], routes: Route[], stationId: string, fuel: string, nowTick: number): Inbound[] {
  const transit = new Map(routes.map((r) => [r.id, r.transit_ticks]));
  return allocations
    .filter((a) => a.destination_station_id === stationId && a.fuel_type === fuel && ON_THE_ROAD.has(a.status))
    .map((a) => ({
      id: a.id,
      tick: Math.max(nowTick, a.expected_arrival_tick ?? a.created_tick + (transit.get(a.route_id) ?? 0)),
      liters: a.quantity,
      departed: a.status === 'IN_TRANSIT',
      estimated: a.expected_arrival_tick == null,
    }))
    .sort((a, b) => a.tick - b.tick);
}

/**
 * Stock path step by step, in the backend's order inside a tick: arrivals first (what does not fit is lost), then demand
 * is served from stock. Demand that finds an empty tank is lost. path[k] is the stock at the start of tick now + k.
 */
function walk(start: number, capacity: number, demand: (k: number) => number, arrivals: Map<number, number>, nowTick: number, horizon: number) {
  let stock = start;
  const path = [stock];
  let empty: Projection['empty'] = null;
  for (let k = 0; k < horizon; k++) {
    stock += Math.min(Math.max(0, capacity - stock), arrivals.get(nowTick + k) ?? 0);
    const d = demand(k);
    if (empty === null && d > stock + 1e-9) empty = { tick: nowTick + k, share: Math.max(0, stock) / d };
    stock -= Math.min(stock, d);
    path.push(stock);
  }
  return { path, empty };
}

export function project(inventory: number, capacity: number, rows: ForecastRow[], nowTick: number, inbound: Inbound[]): Projection | null {
  if (!rows.length) return null;
  const sorted = [...rows].sort((a, b) => a.tick - b.tick);
  const byTick = new Map(sorted.map((r) => [r.tick, r]));
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const rowAt = (tick: number): ForecastRow => byTick.get(tick) ?? (tick < first.tick ? first : last);
  const horizon = Math.max(0, Math.min(FUTURE_TICKS, last.tick - nowTick + 1));
  if (horizon === 0) return null;

  const arrivals = new Map<number, number>();
  for (const a of inbound) arrivals.set(a.tick, (arrivals.get(a.tick) ?? 0) + a.liters);

  const at = (k: number) => rowAt(nowTick + k);
  const mean = walk(inventory, capacity, (k) => at(k).p50, arrivals, nowTick, horizon);
  const hi = walk(inventory, capacity, (k) => at(k).p90, arrivals, nowTick, horizon);
  const lo = walk(inventory, capacity, (k) => at(k).p10, arrivals, nowTick, horizon);

  const points: ProjPoint[] = mean.path.map((m, k) => ({
    tick: nowTick + k,
    mean: m,
    low: hi.path[k],
    high: lo.path[k],
  }));

  let cover = 0;
  for (let k = 0; k < COVER_TICKS; k++) cover += at(k).p50;
  return { points, empty: mean.empty, coverTarget: cover };
}
