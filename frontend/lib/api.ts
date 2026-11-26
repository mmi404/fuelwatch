// Types follow the backend's JSON (backend/fuelwatch/main.py). Only the fields the console reads are listed.

export type Level = 'CRITICAL' | 'WARNING' | 'WATCH' | 'OK' | 'UNKNOWN';

export interface Status {
  mode: 'LIVE' | 'DEGRADED' | 'STALE' | 'STARTING';
  tick: number;
  tick_minutes: number;
  age_seconds: number | null;
  breakers: Record<'read' | 'write', { state: string; opened_total: number }>;
  stream: { state: string };
  execution: { gate_open: boolean };
  autopilot: Autopilot;
  proposals_by_status: Record<string, number>;
  open_alerts: number;
  forecast: { coverage_80: number | null; mape: number | null; forecasts_scored: number };
  epoch: { id: number } | null;
}

export interface Autopilot {
  enabled: boolean;
  min_confidence: number;
  max_liters_per_proposal: number;
  max_liters_per_tick: number;
  max_proposals_per_tick: number;
}

export interface Risk {
  station_id: string;
  fuel_type: string;
  inventory: number;
  capacity: number;
  inbound: number;
  level: Level;
  hours_p50: number | null;
  hours_p90: number | null;
  risk: number | null;
}

export type Fuel = 'DIESEL' | 'PETROL' | 'OCTANE';
export interface Station { id: string; name: string; status: string; region_id: string; demand_profile: string; capacity: Record<string, number>; inventory: Record<string, number> }
export interface Depot { id: string; name: string; status: string; region_id: string; capacity: Record<string, number>; inventory: Record<string, number>; dispatch_capacity_per_tick: number }
export interface Route { id: string; source_depot_id: string; destination_station_id: string; transit_ticks: number; max_shipment: number; status: string }
export interface SupplyArrival { id: string; depot_id: string; fuel_type: string; quantity: number; planned_tick: number; actual_tick: number | null; status: string }
export interface SimEvent { id: number; type: string; start_tick: number; end_tick: number; status: string; parameters: Record<string, unknown> }
export interface SimAllocation {
  id: number; idempotency_key: string; source_depot_id: string; destination_station_id: string; route_id: string; fuel_type: string; quantity: number;
  created_tick: number; departure_tick: number | null; expected_arrival_tick: number | null; actual_arrival_tick: number | null; status: string; failure_reason: string | null;
}
export interface SimMetrics { served_demand_liters: number; unmet_demand_liters: number; service_level: number; allocation_liters: number; allocation_failures: number }
export interface World {
  instance: { id: number; scenario_id: string; scenario_version: string; seed: number; sim_time: string; tick: number; tick_minutes: number; status: string };
  regions: { id: string; name: string; demand_factor: number }[];
  depots: Depot[]; stations: Station[]; routes: Route[]; supply_arrivals: SupplyArrival[]; events: SimEvent[]; allocations: SimAllocation[];
  metrics: SimMetrics; fetched_at: string; stale: boolean; consistent: boolean;
}
export interface Snapshot { meta: Meta; world: World }
/** The `meta` block that rides along on /api/snapshot (a subset of /api/status). */
export interface Meta { mode: Status['mode']; tick: number; tick_minutes: number; age_seconds: number | null }

/** One row of GET /api/forecast: per-tick demand in litres with a P10 to P90 band. */
export interface ForecastRow { station_id: string; fuel_type: string; tick: number; hour: number; p10: number; p50: number; p90: number }
/** One row of GET /api/audit. */
export interface AuditRow { id: number; at: string; actor: string; action: string; entity: string; entity_id: string; detail: Record<string, unknown> }
/** One row of GET /api/allocations: a ledger line plus the simulator's own record when it is in the snapshot. */
export interface LedgerLine {
  id: number; proposal_id: number; depot_id: string; station_id: string; route_id: string; fuel_type: string; proposed_quantity: number; executed_quantity: number | null;
  status: string; failure_code: string | null; idempotency_key: string; snapshot_tick: number; policy: string; proposal_status: string; confidence: number;
  simulator: SimAllocation | null;
}

export interface Line { id: number; depot_id: string; route_id: string; fuel_type: string; proposed_quantity: number; executed_quantity: number | null; status: string; failure_code: string | null }
export interface Proposal {
  id: number;
  /** The simulator run the plan belongs to, to compare with `Status.epoch`: ticks restart with each run. */
  epoch_id?: number;
  station_id: string;
  fuel_type: string;
  status: string;
  status_reason: string | null;
  policy: string;
  confidence: number;
  snapshot_tick: number;
  expires_tick: number;
  lines: Line[];
  explanation: {
    headline: { text: string; level: Level };
    action: { total_liters: number; lines: { depot_id: string; route_id: string; liters: number; transit_ticks: number; arrival_tick: number; binding: string }[] };
    effect: { hours_p90_before: number | null; hours_p90_after: number | null; level_after: Level | null; unmet_avoided_p90_liters: number; window_ticks: number };
    binding_constraints: string[];
    shortfalls: { unmet_liters: number; reason: string }[];
    notes: string[];
    alternatives: { option: string; note?: string; route_id?: string; liters?: number; outlook: { level: Level; hours_p90: number | null; unmet_p90_liters: number } | null }[];
    confidence: { score: number; level: string; reasons: string[] };
  };
}

export interface Alert { id: number; station_id: string; fuel_type: string; level: Level; hours_p90: number | null; state: string; first_tick: number }

/** An open demand anomaly for one station, all fuels together. `summary` is the backend's plain-words account. */
export interface Anomaly { station_id: string; station_name: string; kind: 'explained' | 'unexplained'; direction: 'above' | 'below'; first_tick: number; last_tick: number; summary: string }

/** A late supply delivery or fuel lost on arrival. `summary` is the backend's plain-words account. */
export interface DeliveryFinding { id: string; kind: 'late_delivery' | 'lost_fuel'; state: 'overdue' | 'arrived_late' | 'lost'; place_name: string; last_tick: number; summary: string }

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Reads the backend's two error shapes: {error:{code,message}} and FastAPI's {detail:{code,message}} or {detail:"text"}. */
async function failure(res: Response): Promise<ApiError> {
  let text = res.statusText;
  try {
    const body = await res.json();
    const e = body.error ?? body.detail;
    text = typeof e === 'string' ? e : e ? `${e.code ?? ''} ${e.message ?? ''}`.trim() : text;
  } catch {
    /* keep the status text */
  }
  return new ApiError(res.status, text);
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: 'no-store' });
  if (!res.ok) throw await failure(res);
  return res.json();
}

export async function send<T>(method: 'POST' | 'PUT', path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Operator-Token': token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res);
  return res.json();
}
