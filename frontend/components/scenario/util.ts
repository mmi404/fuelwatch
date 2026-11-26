import { ApiError, SimEvent, World } from '@/lib/api';
import { fuelName, int, pretty } from '@/lib/format';

const UNSURE = 'It may or may not have been applied: check the state on this page before trying again.';

/** Plain-language message for a failed write. It says "nothing was changed" only when that is certain. */
export function describeError(e: unknown): string {
  if (!(e instanceof ApiError)) return `No answer from the console. ${UNSURE}`;
  const detail = e.message ? ` (${e.message})` : '';
  if (e.status === 401) return 'The operator token is missing or wrong, so nothing was changed. Enter it again and retry.';
  if (e.status === 422) return `The server refused the values, so nothing was changed.${detail}`;
  if (e.status === 503 && e.message.startsWith('OPERATOR_TOKEN_NOT_SET')) {
    return 'The server has no operator token configured, so every control is switched off there. Nothing was changed.';
  }
  // Simulator commands are sent once. A timeout does not say whether the simulator acted on it.
  if (e.status === 503 && e.message.startsWith('SIMULATOR_UNAVAILABLE')) return `The simulator did not answer. ${UNSURE}${detail}`;
  if (e.status === 502 && e.message.startsWith('SIMULATOR_ERROR')) return `The simulator answered with an error. ${UNSURE}${detail}`;
  return `The request failed with status ${e.status}. ${UNSURE}`;
}

const trim = (name: string) =>
  name
    .replace(/\s+(Fuel|Industrial|Highway|Regional)?\s*Station$/i, '')
    .replace(/\s+Depot$/i, '')
    .replace(/\s+Division$/i, '');

/** Short display names for the ids that appear in event parameters. */
export interface Names {
  station: (id: string) => string;
  depot: (id: string) => string;
  route: (id: string) => string;
  region: (id: string) => string;
}

export function makeNames(world: World | null): Names {
  const stations = new Map((world?.stations ?? []).map((s) => [s.id, trim(s.name)]));
  const depots = new Map((world?.depots ?? []).map((d) => [d.id, trim(d.name)]));
  const regions = new Map((world?.regions ?? []).map((r) => [r.id, trim(r.name)]));
  const routes = new Map((world?.routes ?? []).map((r) => [r.id, r]));
  const station = (id: string) => stations.get(id) ?? pretty(id);
  const depot = (id: string) => depots.get(id) ?? pretty(id);
  return {
    station,
    depot,
    region: (id) => regions.get(id) ?? pretty(id.replace(/^region-/, '')),
    route: (id) => {
      const r = routes.get(id);
      return r ? `${depot(r.source_depot_id)} to ${station(r.destination_station_id)}` : pretty(id);
    },
  };
}

const list = (v: unknown, fn: (s: string) => string, fallback: string): string => {
  if (!Array.isArray(v) || v.length === 0) return fallback;
  return v.map((x) => fn(String(x))).join(', ');
};
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

const humanize = (t: string) => {
  const s = t.replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
};

/** One line of plain words for an event, from its type and parameters. */
export function describeEvent(e: SimEvent, n: Names): string {
  const p = e.parameters ?? {};
  const used = new Set<string>();
  const take = (...keys: string[]) => keys.forEach((k) => used.add(k));
  const fuels = Array.isArray(p.fuel_types) && p.fuel_types.length ? `, ${p.fuel_types.map((f) => fuelName(String(f))).join(' and ')}` : '';
  let text: string;
  switch (e.type.toLowerCase()) {
    case 'demand_spike': {
      take('multiplier', 'region_ids', 'station_ids');
      const m = num(p.multiplier);
      const where = Array.isArray(p.station_ids) && p.station_ids.length ? list(p.station_ids, n.station, '') : list(p.region_ids, n.region, 'all regions');
      text = `Demand spike${m != null ? ` x${m}` : ''} in ${where}`;
      break;
    }
    case 'shipment_delay': {
      take('delay_ticks', 'depot_ids', 'fuel_types');
      const d = num(p.delay_ticks);
      text = `Supply to ${list(p.depot_ids, n.depot, 'all depots')} delayed${d != null ? ` ${int(d)} ticks` : ''}${fuels}`;
      break;
    }
    case 'route_disruption':
      take('route_ids');
      text = `Route closed: ${list(p.route_ids, n.route, 'all routes')}`;
      break;
    case 'station_outage':
      take('station_ids');
      text = `Station outage: ${list(p.station_ids, n.station, 'all stations')}`;
      break;
    case 'supply_shortfall': {
      take('factor', 'depot_ids', 'fuel_types');
      const f = num(p.factor);
      text = `Supply shortfall at ${list(p.depot_ids, n.depot, 'all depots')}${fuels}${f != null ? `: deliveries cut to ${Math.round(f * 100)}%` : ''}`;
      break;
    }
    case 'depot_constraint':
      take('depot_ids');
      // The simulator only changes the depot's label for this event; nothing is blocked.
      text = `Depot constraint flag at ${list(p.depot_ids, n.depot, 'all depots')} (blocks nothing in this simulator)`;
      break;
    default:
      text = humanize(e.type);
  }
  const rest = Object.entries(p)
    .filter(([k]) => !used.has(k))
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${Array.isArray(v) ? v.join(', ') : String(v)}`);
  return rest.length ? `${text} (${rest.join('; ')})` : text;
}
