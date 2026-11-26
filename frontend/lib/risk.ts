import type { Fuel, Level, Risk } from './api';

export const FUELS: Fuel[] = ['DIESEL', 'PETROL', 'OCTANE'];
export const LEVELS: Level[] = ['CRITICAL', 'WARNING', 'WATCH', 'OK', 'UNKNOWN'];

/** Higher is worse. UNKNOWN ranks between WATCH and OK: it is not good news, but it is not an alarm either. */
const RANK: Record<Level, number> = { CRITICAL: 4, WARNING: 3, WATCH: 2, UNKNOWN: 1.5, OK: 1 };
export const levelRank = (l: Level) => RANK[l];

/** CSS colour for a level. OK is deliberately neutral ("healthy is grey"). */
export const levelColor = (l: Level | 'OUTAGE'): string =>
  ({ CRITICAL: 'var(--crit)', WARNING: 'var(--warn)', WATCH: 'var(--watch)', OK: 'var(--ink-2)', UNKNOWN: 'var(--unknown)', OUTAGE: 'var(--outage)' })[l];

export const fuelColor = (f: string): string => ({ PETROL: 'var(--petrol)', DIESEL: 'var(--diesel)', OCTANE: 'var(--octane)' })[f] ?? 'var(--ink-2)';
export const fuelLetter = (f: string): string => f.charAt(0);

export const pairKey = (stationId: string, fuel: string) => `${stationId}:${fuel}`;
export const splitKey = (key: string): { stationId: string; fuel: string } => {
  const i = key.lastIndexOf(':');
  return { stationId: key.slice(0, i), fuel: key.slice(i + 1) };
};
export const riskKey = (r: Pick<Risk, 'station_id' | 'fuel_type'>) => pairKey(r.station_id, r.fuel_type);

/**
 * Ranking: severity first, then the earliest plausible stockout (P90 hours is the cautious end), then least stock.
 * Stable for equal rows because the final tie-break is the key.
 */
export function rankRisks(risks: Risk[]): Risk[] {
  return [...risks].sort(
    (a, b) =>
      levelRank(b.level) - levelRank(a.level) ||
      (a.hours_p90 ?? 1e9) - (b.hours_p90 ?? 1e9) ||
      a.inventory / a.capacity - b.inventory / b.capacity ||
      riskKey(a).localeCompare(riskKey(b)),
  );
}

export const isOperatorOpen = (status: string) => status === 'OPEN';
export const routeClosed = (status: string) => status !== 'AVAILABLE';
