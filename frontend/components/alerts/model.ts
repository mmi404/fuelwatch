// Pure grouping logic for the alert feed: open alerts -> one entry per station, plus one entry per distinct disruption.
import type { Alert, Level, SimEvent, Snapshot } from '@/lib/api';
import { pretty } from '@/lib/format';
import { isOperatorOpen, levelRank, pairKey, routeClosed } from '@/lib/risk';

/** Backend alerts are `open`, `acknowledged` or `cleared`. Only cleared ones are over. One alert per pair (highest id wins). */
export function openAlerts(alerts: Alert[]): Alert[] {
  const byPair = new Map<string, Alert>();
  for (const a of alerts) {
    if (a.state === 'cleared') continue;
    const k = pairKey(a.station_id, a.fuel_type);
    const prev = byPair.get(k);
    if (!prev || a.id > prev.id) byPair.set(k, a);
  }
  return [...byPair.values()];
}

const hoursOf = (a: Alert) => a.hours_p90 ?? 1e9;

/** Worst first: level, then the earliest cautious hours, then the key. */
export const worstFirst = (a: Alert, b: Alert) =>
  levelRank(b.level) - levelRank(a.level) || hoursOf(a) - hoursOf(b) || pairKey(a.station_id, a.fuel_type).localeCompare(pairKey(b.station_id, b.fuel_type));

export interface Cause {
  key: string;
  /** A closed route or a station out of service: drawn with the outage glyph. */
  closure: boolean;
  title: string;
  /** Sentence fragment for "Likely cause: ..." */
  short: string;
  /** "until 06:00, 2.5 h left" when an event gives the window, else null. */
  window: string | null;
  /** Open alerts that depend on this cause, worst first. */
  alerts: Alert[];
  worst: Level;
  hours: number | null;
}

export interface StationEntry {
  stationId: string;
  name: string;
  alerts: Alert[];
  worst: Level;
  hours: number | null;
  firstTick: number;
  cause: Cause | null;
  moreCauses: number;
}

export function leftText(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  return `${h < 10 ? h.toFixed(1) : Math.round(h)} h`;
}

/** "open for 2.5 h", "open for 1 d 3 h". */
export function openFor(ticks: number, tickMinutes: number): string {
  const h = Math.max(0, (ticks * tickMinutes) / 60);
  if (h < 1) return `${Math.max(0, Math.round(h * 60))} min`;
  if (h < 48) return `${h < 10 ? h.toFixed(1) : Math.round(h)} h`;
  return `${Math.floor(h / 24)} d ${Math.round(h % 24)} h`;
}

const strs = (p: Record<string, unknown>, k: string): string[] | null => {
  const v = p[k];
  return Array.isArray(v) && v.every((x) => typeof x === 'string') ? (v as string[]) : null;
};
const num = (p: Record<string, unknown>, k: string): number | null => (typeof p[k] === 'number' ? (p[k] as number) : null);

const isActive = (e: SimEvent, tick: number) => e.status === 'ACTIVE' || (e.status !== 'RESOLVED' && e.start_tick <= tick && tick <= e.end_tick);

const clock = (simTime: string, nowTick: number, tick: number, tm: number): string | null => {
  const d = new Date(simTime.endsWith('Z') ? simTime : `${simTime}Z`);
  if (Number.isNaN(d.getTime())) return null;
  d.setUTCMinutes(d.getUTCMinutes() + (tick - nowTick) * tm);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};

/** Distinct disruptions visible in the snapshot that touch at least one open alert. */
export function buildCauses(snapshot: Snapshot, alerts: Alert[], tick: number, tm: number): Cause[] {
  const w = snapshot.world;
  const byStation = new Map<string, Alert[]>();
  for (const a of alerts) byStation.set(a.station_id, [...(byStation.get(a.station_id) ?? []), a]);
  const stationName = (id: string) => w.stations.find((s) => s.id === id)?.name ?? pretty(id);
  const destsOfDepots = (depotIds: string[]) => w.routes.filter((r) => depotIds.includes(r.source_depot_id)).map((r) => r.destination_station_id);
  const names = (ids: string[], what: string) => (ids.length ? ids.map(pretty).join(' and ') : what);

  const causes = new Map<string, Omit<Cause, 'worst' | 'hours'>>();
  const add = (key: string, base: Omit<Cause, 'alerts' | 'worst' | 'hours' | 'key'>, stationIds: string[], fuels: string[] | null) => {
    const hit = new Map<number, Alert>();
    for (const sid of new Set(stationIds)) for (const a of byStation.get(sid) ?? []) if (!fuels || fuels.includes(a.fuel_type)) hit.set(a.id, a);
    if (!hit.size) return;
    const prev = causes.get(key);
    if (prev) {
      for (const a of prev.alerts) hit.set(a.id, a);
      causes.set(key, { ...prev, alerts: [...hit.values()], window: base.window ?? prev.window });
    } else causes.set(key, { key, ...base, alerts: [...hit.values()] });
  };

  const routeCause = (routeId: string, window: string | null) => {
    const r = w.routes.find((x) => x.id === routeId);
    if (!r) return;
    const route = `${pretty(r.source_depot_id)} to ${pretty(r.destination_station_id)}`;
    add(`route:${r.id}`, { closure: true, title: `Route closed: ${route}`, short: `the route ${route} is closed`, window }, [r.destination_station_id], null);
  };
  const stationCause = (id: string, window: string | null) =>
    add(`station:${id}`, { closure: true, title: `Station out of service: ${stationName(id)}`, short: `${stationName(id)} is out of service`, window }, [id], null);
  const depotCause = (id: string, status: string, window: string | null) => {
    const word = status === 'CONSTRAINED' ? 'constrained' : status.toLowerCase();
    add(`depot:${id}`, { closure: false, title: `Depot ${word}: ${pretty(id)}`, short: `depot ${pretty(id)} is ${word}`, window }, destsOfDepots([id]), null);
  };

  // Standing state first, then the events that explain it (events add the window).
  for (const r of w.routes) if (routeClosed(r.status)) routeCause(r.id, null);
  for (const s of w.stations) if (!isOperatorOpen(s.status)) stationCause(s.id, null);
  for (const d of w.depots) if (!isOperatorOpen(d.status)) depotCause(d.id, d.status, null);

  for (const e of w.events) {
    if (!isActive(e, tick)) continue;
    const p = e.parameters ?? {};
    const end = clock(w.instance.sim_time, tick, e.end_tick + 1, tm);
    const window = `${end ? `until ${end}, ` : ''}${leftText(Math.max(0, ((e.end_tick + 1 - tick) * tm) / 60))} left`;
    switch (e.type) {
      case 'route_disruption':
        for (const id of strs(p, 'route_ids') ?? []) routeCause(id, window);
        break;
      case 'station_outage':
        for (const id of strs(p, 'station_ids') ?? []) stationCause(id, window);
        break;
      case 'depot_constraint':
        for (const id of strs(p, 'depot_ids') ?? []) depotCause(id, 'CONSTRAINED', window);
        break;
      case 'supply_shortfall': {
        const depots = strs(p, 'depot_ids') ?? w.depots.map((d) => d.id);
        const factor = num(p, 'factor');
        const who = names(strs(p, 'depot_ids') ?? [], 'all depots');
        const at = factor == null ? 'is cut' : `is cut to ${Math.round(factor * 100)}% of plan`;
        add(`event:${e.id}`, { closure: false, title: `Supply shortfall: ${who}${factor == null ? '' : `, ${Math.round(factor * 100)}% of plan`}`, short: `supply from ${who} ${at}`, window }, destsOfDepots(depots), strs(p, 'fuel_types'));
        break;
      }
      case 'shipment_delay': {
        const depots = strs(p, 'depot_ids') ?? w.depots.map((d) => d.id);
        const delay = num(p, 'delay_ticks');
        const by = delay == null ? '' : `${leftText((delay * tm) / 60)}`;
        const who = names(strs(p, 'depot_ids') ?? [], 'all depots');
        add(`event:${e.id}`, { closure: false, title: `Shipment delay: ${who}${by ? `, ${by}` : ''}`, short: `shipments from ${who} are running ${by ? `${by} ` : ''}late`, window }, destsOfDepots(depots), strs(p, 'fuel_types'));
        break;
      }
      case 'demand_spike': {
        const stations = strs(p, 'station_ids');
        const regions = strs(p, 'region_ids');
        const ids = w.stations.filter((s) => (!stations && !regions) || (stations?.includes(s.id) ?? false) || (regions?.includes(s.region_id) ?? false)).map((s) => s.id);
        const mult = num(p, 'multiplier');
        const where = regions ? regions.map((r) => w.regions.find((x) => x.id === r)?.name ?? pretty(r)).join(' and ') : stations ? stations.map(pretty).join(' and ') : 'all stations';
        const x = mult == null ? '' : ` x${mult}`;
        add(`event:${e.id}`, { closure: false, title: `Demand spike${x}: ${where}`, short: `demand is spiking${x} in ${where}`, window }, ids, null);
        break;
      }
      default:
        break;
    }
  }

  return [...causes.values()]
    .map((c): Cause => {
      const sorted = [...c.alerts].sort(worstFirst);
      return { ...c, alerts: sorted, worst: sorted[0].level, hours: sorted[0].hours_p90 };
    })
    .sort((a, b) => levelRank(b.worst) - levelRank(a.worst) || b.alerts.length - a.alerts.length || (a.hours ?? 1e9) - (b.hours ?? 1e9) || a.key.localeCompare(b.key));
}

export function buildStations(snapshot: Snapshot, alerts: Alert[], causes: Cause[]): StationEntry[] {
  const by = new Map<string, Alert[]>();
  for (const a of alerts) by.set(a.station_id, [...(by.get(a.station_id) ?? []), a]);
  const out: StationEntry[] = [];
  for (const [stationId, list] of by) {
    const sorted = [...list].sort(worstFirst);
    const mine = causes
      .map((c) => ({ c, n: c.alerts.filter((a) => a.station_id === stationId).length }))
      .filter((x) => x.n > 0)
      .sort((a, b) => b.n - a.n); // stable: ties keep the cause ranking
    out.push({
      stationId,
      name: snapshot.world.stations.find((s) => s.id === stationId)?.name ?? pretty(stationId),
      alerts: sorted,
      worst: sorted[0].level,
      hours: Math.min(...sorted.map(hoursOf)) >= 1e9 ? null : Math.min(...sorted.map(hoursOf)),
      firstTick: Math.min(...sorted.map((a) => a.first_tick)),
      cause: mine[0]?.c ?? null,
      moreCauses: Math.max(0, mine.length - 1),
    });
  }
  return out.sort((a, b) => levelRank(b.worst) - levelRank(a.worst) || (a.hours ?? 1e9) - (b.hours ?? 1e9) || a.stationId.localeCompare(b.stationId));
}
