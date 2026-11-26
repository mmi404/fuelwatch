import type { AuditRow, LedgerLine, Route, SimAllocation } from '@/lib/api';
import { int, liters, pretty } from '@/lib/format';

/** Ledger line statuses the backend lists (db.ledger): in order of life, then the three that leave that path. */
export const STEPS = ['SENDING', 'SUBMITTED', 'IN_TRANSIT', 'ARRIVED'] as const;
/** UNKNOWN is not final: the backend looks the key up and the line becomes SUBMITTED or FAILED. */
export const OFF_PATH = ['FAILED', 'REJECTED', 'UNKNOWN'] as const;
export const ALL_STATUSES: string[] = [...STEPS, ...OFF_PATH];

export const STATUS_WORD: Record<string, string> = {
  SENDING: 'Sending',
  SUBMITTED: 'Submitted',
  IN_TRANSIT: 'In transit',
  ARRIVED: 'Arrived',
  FAILED: 'Failed',
  REJECTED: 'Rejected',
  UNKNOWN: 'Unknown',
};
export const statusWord = (s: string) => STATUS_WORD[s] ?? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' ');

/** What each status means to the operator, one sentence. Shown in the expanded row. */
export const STATUS_MEANING: Record<string, string> = {
  SENDING: 'The quantity is stored and the request is going out now.',
  SUBMITTED: 'The simulator accepted the request and has not dispatched it yet.',
  IN_TRANSIT: 'The tanker has left the depot and is on the road.',
  ARRIVED: 'The fuel reached the station.',
  FAILED: 'The shipment did not happen. See the reason.',
  REJECTED: 'The simulator refused the request. Nothing was sent.',
  UNKNOWN: 'The reply was lost. The system looks the idempotency key up in the next snapshot instead of guessing.',
};

const FAILURE_TEXT: Record<string, string> = {
  NOT_FOUND_AFTER_LOOKUP: 'no allocation carries this key after the lookup window',
  WORLD_RESET: 'the simulation was reset while this line was open',
  ROUTE_DISRUPTED: 'the route was disrupted',
  ROUTE_UNAVAILABLE: 'the route was not available',
  STATION_CLOSED: 'the station was closed',
  BELOW_MINIMUM: 'the quantity fell below the minimum shipment',
  DEPOT_STOCK: 'the depot did not have enough stock',
  DISPATCH_CAPACITY: 'the depot had no dispatch capacity left this tick',
  HEADROOM: 'the station had no room left to receive it',
  ROUTE_MAX: 'the route maximum was reached',
  ROUTE_MISMATCH: 'the route no longer links this depot and station',
  NOT_FOUND: 'the route, depot or station was not found',
};
export const failureText = (code: string | null | undefined) => (code ? (FAILURE_TEXT[code] ?? code.toLowerCase().replace(/_/g, ' ')) : null);

/** "Gazipur to Tongi" */
export const routeLabel = (line: LedgerLine, stationId: string) => `${pretty(line.depot_id)} to ${pretty(stationId)}`;

export interface Clip { clipped: boolean; text: string }
/** Proposed against executed litres. The backend does not store which limit bound, so only the route maximum can be named. */
export function clipInfo(line: LedgerLine, routes: Route[]): Clip {
  const exec = line.executed_quantity;
  if (exec == null || exec >= line.proposed_quantity) return { clipped: false, text: '' };
  const max = routes.find((r) => r.id === line.route_id)?.max_shipment;
  const byRoute = max != null && Math.round(exec) === Math.round(max);
  return { clipped: true, text: byRoute ? 'clipped by route max' : 'cut at send time by re-validation' };
}

/** The tick a line was created: the simulator's own record when we have it, else the snapshot the proposal was made on. */
export const lineTick = (l: LedgerLine) => l.simulator?.created_tick ?? l.snapshot_tick;

export const confidenceWord = (c: number) => (c >= 0.8 ? 'High' : c >= 0.5 ? 'Medium' : 'Low');

export interface FlightSummary {
  litersOnRoad: number;
  byStatus: Record<string, number>;
  litersByStatus: Record<string, number>;
  next: SimAllocation | null;
}
const FLIGHT = new Set(['PENDING', 'IN_TRANSIT']);
export function flightSummary(allocs: SimAllocation[]): FlightSummary {
  const byStatus: Record<string, number> = {};
  const litersByStatus: Record<string, number> = {};
  let litersOnRoad = 0;
  let next: SimAllocation | null = null;
  for (const a of allocs) {
    byStatus[a.status] = (byStatus[a.status] ?? 0) + 1;
    litersByStatus[a.status] = (litersByStatus[a.status] ?? 0) + a.quantity;
    if (FLIGHT.has(a.status)) {
      litersOnRoad += a.quantity;
      if (a.expected_arrival_tick != null && (next == null || a.expected_arrival_tick < (next.expected_arrival_tick ?? 1e9))) next = a;
    }
  }
  return { litersOnRoad, byStatus, litersByStatus, next };
}

// ---- audit: plain words ----------------------------------------------------------------------

export const ACTORS = ['operator', 'autopilot', 'system'] as const;
export const actorWord = (a: string) => a.toUpperCase();

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const lit = (v: unknown): string | null => {
  const n = num(v);
  return n == null ? null : liters(n);
};

/** One plain-words sentence for an audit row. Everything comes from the row as text; unknown actions fall back to the raw code. */
export function describeAudit(row: AuditRow): string {
  const d = row.detail ?? {};
  const id = row.entity_id;
  const proposal = num(d.proposal_id) ?? (row.entity === 'proposal_line' ? null : id);
  const lineOf = row.entity === 'proposal_line' ? `line ${id}${proposal != null ? ` of proposal ${proposal}` : ''}` : '';
  switch (row.action) {
    case 'proposal.approved': {
      const parts = [lit(d.liters), num(d.snapshot_tick) != null ? `based on tick ${d.snapshot_tick}` : null, d.override_stale === true ? 'stale-data override on' : null].filter(Boolean);
      return `approved proposal ${id}${parts.length ? ` (${parts.join(', ')})` : ''}`;
    }
    case 'proposal.rejected':
      return `rejected proposal ${id}`;
    case 'proposal.expired':
      return `proposal ${id} expired${str(d.reason) ? `: ${d.reason}` : ''}${num(d.tick) != null ? ` at tick ${d.tick}` : ''}`;
    case 'proposal.held':
      return `proposal ${id} held${str(d.reason) ? `: ${d.reason}` : ''}`;
    case 'proposal.resumed':
      return `proposal ${id} resumed after the gate reopened`;
    case 'proposal.executed':
    case 'proposal.partial':
    case 'proposal.failed': {
      const word = row.action.split('.')[1];
      const sent = num(d.sent);
      const total = num(d.lines);
      return `proposal ${id} ${word}${sent != null && total != null ? `: ${sent} of ${total} ${total === 1 ? 'line' : 'lines'} sent` : ''}`;
    }
    case 'gate.override':
      return `sent proposal ${id} despite degraded data (override)${num(d.snapshot_tick) != null ? ` (tick ${d.snapshot_tick})` : ''}`;
    case 'line.submitted': {
      const parts = [lit(d.liters), lit(d.approved_liters) && num(d.approved_liters) !== num(d.liters) ? `approved ${lit(d.approved_liters)}` : null].filter(Boolean);
      return `${lineOf} submitted${num(d.allocation_id) != null ? ` as simulator allocation ${d.allocation_id}` : ''}${parts.length ? ` (${parts.join(', ')})` : ''}`;
    }
    case 'line.rejected':
      return `simulator rejected ${lineOf}${str(d.code) ? `: ${d.code}` : ''}${lit(d.liters) ? ` (${lit(d.liters)})` : ''}`;
    case 'line.skipped':
      return `skipped ${lineOf}${str(d.reason) ? `: ${failureText(String(d.reason))}` : ''}${lit(d.approved_liters) ? ` (approved ${lit(d.approved_liters)})` : ''}`;
    case 'line.unknown':
      return `no reply for ${lineOf}; will look up its key${str(d.error) ? ` (${d.error})` : ''}`;
    case 'line.resolved':
      return `${lineOf} resolved${num(d.allocation_id) != null ? `: simulator allocation ${d.allocation_id}` : ''}${str(d.how) ? `, ${d.how}` : ''}`;
    case 'line.failed':
      return `${lineOf} failed${str(d.reason) ? `: ${d.reason}` : ''}`;
    case 'autopilot.changed': {
      const on = d.enabled === true;
      const lim = [
        num(d.min_confidence) != null ? `min confidence ${Math.round(Number(d.min_confidence) * 100)}%` : null,
        lit(d.max_liters_per_proposal) ? `${lit(d.max_liters_per_proposal)} per proposal` : null,
        lit(d.max_liters_per_tick) ? `${lit(d.max_liters_per_tick)} per tick` : null,
        num(d.max_proposals_per_tick) != null ? `${int(Number(d.max_proposals_per_tick))} proposals per tick` : null,
      ].filter(Boolean);
      return `autopilot ${on ? 'on' : 'off'}${lim.length ? `, limits: ${lim.join(', ')}` : ''}`;
    }
    case 'sim.run':
      return 'started the simulator clock';
    case 'sim.pause':
      return 'paused the simulator clock';
    case 'sim.step':
      return 'stepped the simulator one tick';
    case 'sim.reset':
      return 'reset the simulation';
    default: {
      const keys = Object.entries(d).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v)).slice(0, 4).map(([k, v]) => `${k} ${String(v)}`);
      return `${row.action} on ${row.entity} ${id}${keys.length ? ` (${keys.join(', ')})` : ''}`;
    }
  }
}

/** Audit timestamps are real wall-clock UTC instants. Fixed zone, so server and client agree. */
export function auditParts(iso: string): { day: string; time: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: '', time: iso };
  const p = (n: number) => String(n).padStart(2, '0');
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
  return { day: `${p(d.getUTCDate())} ${mon} ${d.getUTCFullYear()}`, time: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}` };
}
