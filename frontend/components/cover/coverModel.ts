import type { Level, Risk } from '@/lib/api';
import { hours } from '@/lib/format';

/** Right edge of the shared cover axis, in hours: the backend looks 96 ticks (24 h) ahead. Every row draws on 0 to AXIS_H. */
export const AXIS_H = 24;
/**
 * Guide bands on the axis, in hours, following the backend's level cut-offs. The CRITICAL cut-off is the larger of 4 h and the
 * best route's transit plus 4 ticks, so these are guides only: the level shown on a row is always the backend's.
 */
export const BANDS: { name: string; level: Level; from: number; to: number }[] = [
  { name: 'Crit', level: 'CRITICAL', from: 0, to: 4 },
  { name: 'Warn', level: 'WARNING', from: 4, to: 12 },
  { name: 'Watch', level: 'WATCH', from: 12, to: AXIS_H },
];
export const TICKS_H = [0, 4, 12, 24];

/** Hours to a percentage of the axis, clamped to the axis. */
export const axisPct = (h: number) => (Math.min(Math.max(h, 0), AXIS_H) / AXIS_H) * 100;

export type CoverShape =
  | { kind: 'unknown' }
  /** Inventory is at 0: a solid marker at 0. */
  | { kind: 'empty' }
  /** No stockout expected inside the axis and no estimate to draw: only an open arrow at the right edge. */
  | { kind: 'beyond' }
  | { kind: 'bar'; start: number; end: number; expected: number | null; open: boolean };

/** What to draw on the shared axis for one row. Percentages are of the axis width. */
export function coverShape(r: Risk): CoverShape {
  if (r.level === 'UNKNOWN') return { kind: 'unknown' };
  if (r.inventory <= 0) return { kind: 'empty' };
  const { hours_p50: p50, hours_p90: p90 } = r;
  if (p50 == null && p90 == null) return { kind: 'beyond' };
  // p90 is the cautious (earlier) end; a null expected time means no stockout within the horizon.
  const a = p90 ?? (p50 as number);
  const b = p50 ?? Infinity;
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  if (lo >= AXIS_H) return { kind: 'beyond' };
  return { kind: 'bar', start: axisPct(lo), end: axisPct(hi), expected: p50 != null && p50 < AXIS_H ? axisPct(p50) : null, open: hi >= AXIS_H };
}

/** The figure at the right of the bar: the expected time in big type, the cautious time as text. */
export function coverFigure(r: Risk): { primary: string; secondary: string; empty: boolean } {
  if (r.level === 'UNKNOWN') return { primary: 'no data', secondary: '', empty: false };
  if (r.inventory <= 0) return { primary: 'EMPTY', secondary: '0 L on hand', empty: true };
  const { hours_p50: p50, hours_p90: p90 } = r;
  const primary = p50 === 0 ? 'now' : hours(p50);
  let secondary: string;
  if (p50 == null && p90 == null) secondary = `none in ${AXIS_H} h`;
  else if (p90 != null && p50 != null && Math.abs(p90 - p50) < 0.05) secondary = 'no spread';
  else secondary = `cautious ${p90 === 0 ? 'now' : hours(p90)}`;
  return { primary, secondary, empty: false };
}

/** "2 critical, 4 warning, 1 watch, 5 OK" for the table's accessible name. Zero counts are left out. */
export function levelSummary(risks: Risk[]): string {
  const order: [Level, string][] = [['CRITICAL', 'critical'], ['WARNING', 'warning'], ['WATCH', 'watch'], ['UNKNOWN', 'unknown'], ['OK', 'OK']];
  const parts = order.map(([l, w]) => [risks.filter((r) => r.level === l).length, w] as const).filter(([c]) => c > 0).map(([c, w]) => `${c} ${w}`);
  return parts.length ? parts.join(', ') : 'no pairs';
}
