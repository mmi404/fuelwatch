// Formatting helpers. All fixed-locale and fixed-timezone so server and client render identical text (no hydration mismatch).

const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 });

/** 12,345 */
export const int = (n: number | null | undefined) => (n == null ? 'n/a' : nf0.format(Math.round(n)));
/** 12,345 L */
export const liters = (n: number | null | undefined) => (n == null ? 'n/a' : `${nf0.format(Math.round(n))} L`);
/** 12.3 kL for big volumes, plain litres below 10,000 */
export const volume = (n: number | null | undefined) => (n == null ? 'n/a' : Math.abs(n) >= 10000 ? `${nf1.format(n / 1000)} kL` : `${nf0.format(Math.round(n))} L`);
/** 0.973 -> 97.3% */
export const pct = (x: number | null | undefined, digits = 1) => (x == null ? 'n/a' : `${(x * 100).toFixed(digits)}%`);
/** +1.2 / -0.4 percentage points */
export const points = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)} pt`;

/** Hours of cover as a short figure: "6.5 h", "45 min", "24+ h". `cap` is how far the backend looks ahead; null means no stockout within it. */
export function hours(h: number | null | undefined, cap = 24): string {
  if (h == null) return `${cap}+ h`;
  if (h <= 0) return 'now';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min`;
  if (h >= cap) return `${cap}+ h`;
  return `${h < 10 ? h.toFixed(1) : Math.round(h)} h`;
}

/** Ticks to a duration in hours: 2 ticks of 15 min = "0.5 h". */
export const tickHours = (ticks: number, tickMinutes: number) => (ticks * tickMinutes) / 60;

/** Simulated clock "Jan 01 06:30" from the simulator's sim_time (no timezone: it is simulated). */
export function simClock(simTime: string | null | undefined): string {
  if (!simTime) return '--';
  const d = new Date(simTime.endsWith('Z') ? simTime : `${simTime}Z`);
  if (Number.isNaN(d.getTime())) return simTime;
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()];
  const p = (n: number) => String(n).padStart(2, '0');
  return `${mon} ${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Clock time of a tick relative to now: tick 12 at 15 min/tick from tick 0 at sim_time. */
export function tickClock(simTime: string | null | undefined, nowTick: number, tick: number, tickMinutes: number): string {
  if (!simTime) return `tick ${tick}`;
  const d = new Date(simTime.endsWith('Z') ? simTime : `${simTime}Z`);
  if (Number.isNaN(d.getTime())) return `tick ${tick}`;
  d.setUTCMinutes(d.getUTCMinutes() + (tick - nowTick) * tickMinutes);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Data age in whole seconds: "4 s", "2 min", "1 h 5 min". */
export function age(seconds: number | null | undefined): string {
  if (seconds == null) return 'no data';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** ISO instant to "HH:MM:SS" in UTC. */
export function clockUtc(iso: string | null | undefined): string {
  if (!iso) return '--';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

/** "station-mirpur" -> "Mirpur"; "route-gazipur-mirpur" -> "Gazipur to Mirpur" is built by callers from the parts. */
export const pretty = (id: string) => {
  const t = id.replace(/^(station|depot|route)-/, '').replace(/-/g, ' ');
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export const fuelName = (f: string) => f.charAt(0) + f.slice(1).toLowerCase();
export const plural = (n: number, one: string, many = `${one}s`) => `${int(n)} ${n === 1 ? one : many}`;
