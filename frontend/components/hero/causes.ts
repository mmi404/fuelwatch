import type { SimEvent, World } from '@/lib/api';
import { int, pretty, tickHours } from '@/lib/format';
import { routeClosed, isOperatorOpen } from '@/lib/risk';

/** One root cause with its blast radius, ready to render as a short line. */
export interface Cause {
  key: string;
  /** Glyph family: a closure, a degraded condition, or something scheduled. */
  tone: 'OUTAGE' | 'WARNING' | 'WATCH';
  /** "Route closed: Gazipur to Mirpur" */
  title: string;
  /** Station-fuel pairs downstream of the cause. */
  pairs: number;
  /** Secondary line: window and time left, or a parameter. */
  meta: string;
}

const EVENT_LABEL: Record<string, string> = {
  route_disruption: 'Route disruption',
  station_outage: 'Station outage',
  demand_spike: 'Demand spike',
  shipment_delay: 'Shipment delay',
  depot_constraint: 'Depot constraint',
  supply_shortfall: 'Supply shortfall',
};

const strings = (p: Record<string, unknown>, key: string): string[] | null => {
  const v = p[key];
  if (!Array.isArray(v)) return null;
  const out = v.filter((x): x is string => typeof x === 'string');
  return out.length ? out : null;
};
const number = (p: Record<string, unknown>, key: string): number | null => (typeof p[key] === 'number' ? (p[key] as number) : null);

const routeName = (w: World, id: string) => {
  const r = w.routes.find((x) => x.id === id);
  return r ? `${pretty(r.source_depot_id)} to ${pretty(r.destination_station_id)}` : pretty(id);
};

/** Pairs at the given stations, limited to the fuels the event names (all fuels when it names none). */
function pairCount(w: World, stationIds: Set<string>, fuels: string[] | null): number {
  let n = 0;
  for (const s of w.stations) {
    if (!stationIds.has(s.id)) continue;
    const have = Object.keys(s.capacity);
    n += fuels ? have.filter((f) => fuels.includes(f)).length : have.length;
  }
  return n;
}

const names = (list: string[], max = 2) => (list.length <= max ? list.join(', ') : `${list.slice(0, max).join(', ')} +${list.length - max}`);

/** Stations hit by an event: through its routes, its region, its depots, or the stations it names. Empty parameters mean the whole network. */
function eventScope(w: World, e: SimEvent): { target: string; stations: Set<string> } {
  const p = e.parameters ?? {};
  const all = new Set(w.stations.map((s) => s.id));
  const viaRoutes = (routes: typeof w.routes) => new Set(routes.map((r) => r.destination_station_id));
  switch (e.type) {
    case 'route_disruption': {
      const ids = strings(p, 'route_ids');
      if (!ids) return { target: 'all routes', stations: all };
      return { target: names(ids.map((id) => routeName(w, id))), stations: viaRoutes(w.routes.filter((r) => ids.includes(r.id))) };
    }
    case 'station_outage': {
      const ids = strings(p, 'station_ids');
      if (!ids) return { target: 'all stations', stations: all };
      return { target: names(ids.map(pretty)), stations: new Set(ids) };
    }
    case 'demand_spike': {
      const ids = strings(p, 'region_ids');
      if (!ids) return { target: 'whole network', stations: all };
      const label = ids.map((id) => w.regions.find((r) => r.id === id)?.name ?? pretty(id.replace(/^region-/, '')));
      return { target: names(label), stations: new Set(w.stations.filter((s) => ids.includes(s.region_id)).map((s) => s.id)) };
    }
    default: {
      const ids = strings(p, 'depot_ids');
      if (!ids) return { target: 'all depots', stations: all };
      return { target: names(ids.map(pretty)), stations: viaRoutes(w.routes.filter((r) => ids.includes(r.source_depot_id))) };
    }
  }
}

const span = (ticks: number, tm: number) => `${int(ticks)} ${ticks === 1 ? 'tick' : 'ticks'} (${tickHours(ticks, tm).toFixed(tickHours(ticks, tm) < 10 ? 1 : 0)} h)`;

/**
 * Active or scheduled events, then routes, depots and stations that are not open and are not already named by one of those events.
 * Blast radius counts station-fuel pairs downstream of the cause.
 */
export function buildCauses(w: World, tick: number): Cause[] {
  const tm = w.instance.tick_minutes;
  const out: Cause[] = [];
  const namedRoutes = new Set<string>();
  const namedStations = new Set<string>();
  const namedDepots = new Set<string>();

  for (const e of w.events) {
    if (e.status !== 'ACTIVE' && e.status !== 'SCHEDULED') continue;
    const p = e.parameters ?? {};
    strings(p, 'route_ids')?.forEach((id) => namedRoutes.add(id));
    strings(p, 'station_ids')?.forEach((id) => namedStations.add(id));
    strings(p, 'depot_ids')?.forEach((id) => namedDepots.add(id));
    const { target, stations } = eventScope(w, e);
    const pairs = pairCount(w, stations, strings(p, 'fuel_types'));
    const scheduled = e.status === 'SCHEDULED';
    const mult = number(p, 'multiplier');
    const factor = number(p, 'factor');
    const delay = number(p, 'delay_ticks');
    const detail = [mult != null && `x${mult}`, factor != null && `${Math.round(factor * 100)}% of supply`, delay != null && `${int(delay)} ticks late`].filter(Boolean).join(', ');
    const when = scheduled ? `starts in ${span(Math.max(0, e.start_tick - tick), tm)}` : `${span(Math.max(0, e.end_tick + 1 - tick), tm)} left`;
    out.push({
      key: `event-${e.id}`,
      tone: scheduled ? 'WATCH' : e.type === 'route_disruption' || e.type === 'station_outage' ? 'OUTAGE' : 'WARNING',
      title: `${EVENT_LABEL[e.type] ?? pretty(e.type)}${scheduled ? ' (scheduled)' : ''}: ${target}`,
      pairs,
      meta: [`ticks ${e.start_tick} to ${e.end_tick}`, when, detail].filter(Boolean).join(' / '),
    });
  }

  for (const r of w.routes) {
    if (!routeClosed(r.status) || namedRoutes.has(r.id)) continue;
    out.push({
      key: `route-${r.id}`,
      tone: 'OUTAGE',
      title: `Route ${r.status.toLowerCase().replace(/_/g, ' ')}: ${routeName(w, r.id)}`,
      pairs: pairCount(w, new Set([r.destination_station_id]), null),
      meta: `${r.status}, no event window reported`,
    });
  }
  for (const d of w.depots) {
    if (isOperatorOpen(d.status) || namedDepots.has(d.id)) continue;
    const dest = new Set(w.routes.filter((r) => r.source_depot_id === d.id).map((r) => r.destination_station_id));
    out.push({ key: `depot-${d.id}`, tone: 'OUTAGE', title: `Depot ${d.status.toLowerCase().replace(/_/g, ' ')}: ${pretty(d.id)}`, pairs: pairCount(w, dest, null), meta: `${d.status}, supplies ${int(dest.size)} ${dest.size === 1 ? 'station' : 'stations'}` });
  }
  for (const s of w.stations) {
    if (isOperatorOpen(s.status) || namedStations.has(s.id)) continue;
    out.push({ key: `station-${s.id}`, tone: 'OUTAGE', title: `Station ${s.status.toLowerCase().replace(/_/g, ' ')}: ${pretty(s.id)}`, pairs: pairCount(w, new Set([s.id]), null), meta: `${s.status}, served demand is zero` });
  }

  // Active before scheduled, then widest blast radius first.
  const order = (c: Cause) => (c.tone === 'WATCH' ? 1 : 0);
  return out.sort((a, b) => order(a) - order(b) || b.pairs - a.pairs || a.key.localeCompare(b.key));
}
