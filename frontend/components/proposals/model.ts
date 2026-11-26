// Reads a proposal's explanation card defensively and turns it into the words the operator sees.
// The backend card has more fields than lib/api.ts types, so everything is read through narrow helpers.

import { ApiError } from '@/lib/api';
import type { Level, Proposal } from '@/lib/api';
import { age, fuelName, hours, liters, pct, plural, pretty, tickClock } from '@/lib/format';

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Rec) : {});
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const LEVELS: readonly string[] = ['CRITICAL', 'WARNING', 'WATCH', 'OK', 'UNKNOWN'];
const level = (v: unknown): Level | null => (typeof v === 'string' && LEVELS.includes(v) ? (v as Level) : null);

export type Confidence = 'High' | 'Medium' | 'Low';

export interface PlanLine {
  depotId: string;
  routeId: string;
  liters: number;
  executed: number | null;
  status: string;
  failureCode: string | null;
  transitTicks: number | null;
  arrivalTick: number | null;
}
export interface Outlook {
  level: Level | null;
  hoursP50: number | null;
  hoursP90: number | null;
  unmet: number | null;
}
export interface Alt {
  option: string;
  note: string;
  routeId: string;
  depotId: string;
  liters: number | null;
  arrivalTick: number | null;
  outlook: Outlook | null;
}
export interface CardEvent { type: string; startTick: number | null; endTick: number | null }
export interface CardView {
  headline: string;
  level: Level | null;
  hoursP90: number | null;
  lines: PlanLine[];
  total: number;
  effect: { before: number | null; after: number | null; levelAfter: Level | null; avoided: number | null; windowTicks: number | null };
  inventory: number | null;
  capacity: number | null;
  inbound: number | null;
  expectedDemand: number | null;
  p90Demand: number | null;
  events: CardEvent[];
  supply: { depotId: string; liters: number; tick: number | null }[];
  binding: string[];
  shortfalls: { liters: number; reason: string }[];
  notes: string[];
  alternatives: Alt[];
  confidence: { score: number; level: Confidence; reasons: string[] };
  fallback: string | null;
}

function outlook(v: unknown): Outlook | null {
  if (!v || typeof v !== 'object') return null;
  const o = rec(v);
  return { level: level(o.level), hoursP50: num(o.hours_p50), hoursP90: num(o.hours_p90), unmet: num(o.unmet_p90_liters) };
}

export function confidenceWord(score: number): Confidence {
  return score >= 0.8 ? 'High' : score >= 0.5 ? 'Medium' : 'Low';
}

export function readCard(p: Proposal): CardView {
  const x = rec(p.explanation);
  const head = rec(x.headline);
  const act = rec(x.action);
  const eff = rec(x.effect);
  const sig = rec(x.signals);
  const conf = rec(x.confidence);
  const planned = arr(act.lines).map(rec);

  const lines: PlanLine[] = (p.lines.length ? p.lines : []).map((l) => {
    const m = planned.find((a) => a.depot_id === l.depot_id && a.route_id === l.route_id);
    return {
      depotId: l.depot_id,
      routeId: l.route_id,
      liters: l.proposed_quantity,
      executed: l.executed_quantity,
      status: l.status,
      failureCode: l.failure_code,
      transitTicks: m ? num(m.transit_ticks) : null,
      arrivalTick: m ? num(m.arrival_tick) : null,
    };
  });
  if (!lines.length) {
    for (const a of planned) {
      lines.push({ depotId: str(a.depot_id), routeId: str(a.route_id), liters: num(a.liters) ?? 0, executed: null, status: 'PLANNED', failureCode: null, transitTicks: num(a.transit_ticks), arrivalTick: num(a.arrival_tick) });
    }
  }

  const reasons = arr(conf.reasons).map(str).filter(Boolean);
  const notes = arr(x.notes).map(str).filter(Boolean);
  const score = num(conf.score) ?? p.confidence;
  const word = ['High', 'Medium', 'Low'].includes(str(conf.level)) ? (str(conf.level) as Confidence) : confidenceWord(score);
  const fallback = [...reasons, ...notes].find((t) => /allocator failed|fall(s|en)? ?back|simpler policy/i.test(t)) ?? null;

  return {
    headline: str(head.text),
    level: level(head.level),
    hoursP90: num(head.hours_p90),
    lines,
    total: lines.reduce((s, l) => s + l.liters, 0),
    effect: {
      before: num(eff.hours_p90_before),
      after: num(eff.hours_p90_after),
      levelAfter: level(eff.level_after),
      avoided: num(eff.unmet_avoided_p90_liters),
      windowTicks: num(eff.window_ticks),
    },
    inventory: num(sig.inventory),
    capacity: num(sig.capacity),
    inbound: num(sig.inbound_liters),
    expectedDemand: num(sig.expected_demand_until_arrival),
    p90Demand: num(sig.p90_demand_until_arrival),
    events: arr(sig.events).map(rec).map((e) => ({ type: str(e.type), startTick: num(e.start_tick), endTick: num(e.end_tick) })),
    supply: arr(sig.scheduled_supply)
      .map(rec)
      .map((s) => ({ depotId: str(s.depot_id), liters: num(s.liters) ?? 0, tick: num(s.actual_tick) ?? num(s.planned_tick) })),
    binding: arr(x.binding_constraints).map(str).filter(Boolean),
    shortfalls: arr(x.shortfalls)
      .map(rec)
      .map((s) => ({ liters: num(s.unmet_liters) ?? 0, reason: str(s.reason) })),
    notes,
    alternatives: arr(x.alternatives)
      .map(rec)
      .map((a) => ({
        option: str(a.option),
        note: str(a.note),
        routeId: str(a.route_id),
        depotId: str(a.depot_id),
        liters: num(a.liters),
        arrivalTick: num(a.arrival_tick),
        outlook: outlook(a.outlook),
      })),
    confidence: { score, level: word, reasons },
    fallback,
  };
}

/** What a plan-time note needs from the live data. A null means the live data does not say. */
export interface PlanNow {
  tick: number | null;
  tickMinutes: number | null;
  epochId: number | null;
  /** The snapshot's clock: `simTime` is the time at `simTick`. */
  simTick: number;
  simTime: string | null;
}

/**
 * Completes "figures from when the plan was made: ". The card's figures are never recalculated, so this says when they
 * were taken and how old that is in simulated time. When the live data cannot give the age, it says so instead.
 */
export function planMadeAt(p: Pick<Proposal, 'snapshot_tick' | 'epoch_id'>, now: PlanNow): string {
  const made = p.snapshot_tick;
  const unknown = (why: string) => `tick ${made}. Their age is unknown: ${why}`;
  if (p.epoch_id != null && now.epochId != null && p.epoch_id !== now.epochId) return unknown('the plan and the live data are from different simulator runs');
  if (now.tick == null) return unknown('the live tick is not known');
  if (made > now.tick) return unknown(`the live data is at tick ${now.tick}, earlier than the plan`);
  const tm = now.tickMinutes;
  const clock = now.simTime && tm ? tickClock(now.simTime, now.simTick, made, tm) : null;
  const when = clock && clock !== `tick ${made}` ? `${clock} (tick ${made})` : `tick ${made}`;
  const ticks = now.tick - made;
  if (ticks === 0) return `${when}, the current tick`;
  return `${when}, ${tm ? age(ticks * tm * 60) : plural(ticks, 'tick')} ago in simulated time`;
}

/** "Send 12,000 L diesel from Gazipur to Tongi". Several depots: "Send 5,000 L diesel to Tongi: 3,000 L from Gazipur, 2,000 L from Patiya". */
export function actionSentence(p: Proposal, v: CardView): string {
  const fuel = fuelName(p.fuel_type).toLowerCase();
  const to = pretty(p.station_id);
  if (v.lines.length <= 1) return `Send ${liters(v.total)} ${fuel} from ${v.lines[0] ? pretty(v.lines[0].depotId) : 'a depot'} to ${to}`;
  return `Send ${liters(v.total)} ${fuel} to ${to}: ${v.lines.map((l) => `${liters(l.liters)} from ${pretty(l.depotId)}`).join(', ')}`;
}

export const CONFIDENCE_ADVICE: Record<Confidence, string> = {
  High: 'Safe to approve if the facts match what you see.',
  Medium: 'Check the reasons below, then approve.',
  Low: 'Check the reasons first. Consider waiting for fresher data.',
};

export const POLICY: Record<string, string> = { lp: 'LP optimiser', greedy: 'Greedy', rules: 'Rules' };

const BINDING: Record<string, string> = {
  route_max: 'route maximum shipment',
  depot_stock: 'depot stock',
  dispatch_capacity: 'depot dispatch rate',
  headroom: 'room in the station tank',
  approved: 'approved quantity',
};
export const bindingLabel = (b: string) => BINDING[b] ?? b.replace(/_/g, ' ');

const EVENT: Record<string, string> = { DEMAND_SPIKE: 'Demand spike', ROUTE_DISRUPTION: 'Route disruption', STATION_OUTAGE: 'Station outage' };

/** Hours of cover with the "no stockout" and "already out" cases said in words. */
export function cover(h: number | null): string {
  if (h == null) return 'no stockout within 24 h';
  if (h <= 0) return '0 h (already out)';
  return hours(h);
}

export interface Evidence { key: string; text: string; prio: number }

/** Two to four chips: the facts that most explain why the plan looks like it does. */
export function evidence(v: CardView, clock: (tick: number) => string): Evidence[] {
  const out: Evidence[] = [];
  for (const s of v.shortfalls) out.push({ key: `short-${s.reason}`, prio: 1, text: `Short ${liters(s.liters)}: ${s.reason.replace(/_/g, ' ')}` });
  if (v.binding.length) out.push({ key: 'binding', prio: 2, text: `Sized by ${v.binding.map(bindingLabel).join(', ')}` });
  for (const e of v.events) {
    const label = EVENT[e.type] ?? e.type.toLowerCase().replace(/_/g, ' ');
    out.push({ key: `ev-${e.type}-${e.startTick}`, prio: 3, text: e.endTick != null ? `${label} until ${clock(e.endTick + 1)}` : label });
  }
  if (v.inventory != null && v.capacity) out.push({ key: 'stock', prio: 4, text: `Stock ${liters(v.inventory)} of ${liters(v.capacity)} (${pct(v.inventory / v.capacity, 0)})` });
  if (v.expectedDemand != null) out.push({ key: 'demand', prio: 5, text: `Demand until arrival ${liters(v.expectedDemand)} expected${v.p90Demand != null ? `, ${liters(v.p90Demand)} at P90` : ''}` });
  if (v.inbound != null && v.inbound > 0) out.push({ key: 'inbound', prio: 6, text: `${liters(v.inbound)} already on the way` });
  else if (v.supply[0]) out.push({ key: 'supply', prio: 6, text: `Depot restock ${liters(v.supply[0].liters)}${v.supply[0].tick != null ? ` due ${clock(v.supply[0].tick)}` : ''}` });
  else if (v.inbound === 0) out.push({ key: 'inbound', prio: 7, text: 'Nothing inbound' });
  return out.sort((a, b) => a.prio - b.prio).slice(0, 4);
}

export const altTitle = (a: Alt): string =>
  a.option === 'do_nothing' ? 'Do nothing' : a.option === 'wait_one_tick' ? 'Wait one tick' : a.option === 'other_route' ? `Send ${a.liters != null ? liters(a.liters) : ''} from ${a.depotId ? pretty(a.depotId) : 'another depot'} instead`.replace('  ', ' ') : a.option.replace(/_/g, ' ');

// ----- states -----

export type Bucket = 'awaiting' | 'active' | 'history';
export const isOpen = (status: string) => status === 'PROPOSED' || status === 'HELD';
const ON_THE_ROAD = ['SENDING', 'UNKNOWN', 'SUBMITTED', 'IN_TRANSIT', 'PENDING'];
export const stillMoving = (p: Proposal) => p.lines.some((l) => ON_THE_ROAD.includes(l.status));

export const STATE_WORD: Record<string, string> = {
  PROPOSED: 'Awaiting decision',
  HELD: 'Held',
  APPROVED: 'Approved',
  EXECUTING: 'Sending',
  EXECUTED: 'Dispatched',
  PARTIAL: 'Partly sent',
  FAILED: 'Failed',
  REJECTED: 'Rejected',
  EXPIRED: 'Expired',
  SUPERSEDED: 'Superseded',
};

const LINE_STATE: Record<string, string> = {
  PLANNED: 'waiting',
  SENDING: 'sending',
  SUBMITTED: 'accepted by the simulator',
  IN_TRANSIT: 'on the road',
  ARRIVED: 'arrived',
  SKIPPED: 'not sent',
  REJECTED: 'refused by the simulator',
  FAILED: 'failed',
  UNKNOWN: 'sent, outcome not yet confirmed',
};
export const lineState = (s: string) => LINE_STATE[s] ?? s.toLowerCase();

const SKIP: Record<string, string> = {
  BELOW_MINIMUM: 'the quantity that still fits is below the 1,000 L minimum',
  ROUTE_DISRUPTED: 'the route is disrupted',
  STATION_CLOSED: 'the station is closed',
  DEPOT_STOCK: 'the depot no longer has the stock',
  DISPATCH_CAPACITY: 'the depot has no dispatch capacity left this tick',
  HEADROOM: 'the station tank no longer has room',
  ROUTE_MISMATCH: 'the route does not match the depot and station',
  NOT_FOUND: 'the route, depot or station was not found',
  WORLD_RESET: 'the world was reset',
  NOT_FOUND_AFTER_LOOKUP: 'the simulator has no record of it',
};
export const failureText = (code: string | null) => (code ? (SKIP[code] ?? code.replace(/_/g, ' ').toLowerCase()) : '');

// ----- write errors -----

export interface WriteError { headline: string; detail: string }

export function describeError(e: unknown, tick: number | null): WriteError {
  if (e instanceof ApiError) {
    if (e.status === 401) return { headline: 'Token rejected. Check it and retry.', detail: '' };
    if (e.status === 503) return { headline: 'Writes are disabled until the server has an operator token.', detail: '' };
    if (e.status === 404 || e.status === 409 || e.status === 422) return { headline: `State changed${tick != null ? ` (tick ${tick})` : ''}. Review the latest and retry.`, detail: e.message };
    return { headline: `Not applied (HTTP ${e.status}). Retry.`, detail: e.message };
  }
  return { headline: 'Not applied. Retry.', detail: 'The request did not reach the server or no answer came back.' };
}
