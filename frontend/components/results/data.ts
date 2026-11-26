// Reads data/bench.json, which scripts/build-bench-data.mjs writes from bench/results. Every figure on the page comes
// from there or is arithmetic on it: nothing is typed in by hand.
import raw from '@/data/bench.json';

export type PolicyId = 'none' | 'rules' | 'greedy' | 'lp';

export interface Cell {
  scenario: string;
  policy: PolicyId;
  service_level: number | null;
  unmet_liters: number | null;
  shipments: number | null;
  litres_shipped: number | null;
  posts_rejected: number;
  failed_allocations: number | null;
  litres_lost_at_arrival: number | null;
  stockout_pair_hours: number | null;
  /** Null where the run did not record the policy's own time. */
  policy_p95_ms: number | null;
  fallbacks: number | null;
  source_run: string;
  superseded: { source_run: string; same_history_hash: boolean; same_service_level: boolean }[];
}

export interface Scenario { id: string; kind: 'simulator' | 'stress'; what: string }
export interface Run { id: string; generated_at: string; git_commit: string; cells: number; scenarios: string[]; policies: string[] }

export interface Bench {
  horizon_ticks: number | null;
  seed: number | null;
  policies: PolicyId[];
  scenarios: Scenario[];
  runs: Run[];
  reproducibility: { run: string; key: string; reproduced: boolean }[];
  cells: Cell[];
}

export const bench = raw as unknown as Bench;

export const POLICY_NAME: Record<PolicyId, string> = { none: 'Do nothing', rules: 'rules', greedy: 'greedy', lp: 'lp' };
export const POLICY_WHAT: Record<PolicyId, string> = {
  none: 'Nothing is allocated. The baseline.',
  rules: 'Fills each critical tank as far as the limits allow, by the fastest open route. The last fallback.',
  greedy: 'Tops up one tank at a time, the most urgent first.',
  lp: 'A rolling-horizon linear program. The live default.',
};

export const cell = (scenario: string, policy: PolicyId): Cell | undefined => bench.cells.find((c) => c.scenario === scenario && c.policy === policy);

/** The allocating policies measured in a scenario, in a fixed order. */
export const allocating = (scenario: string): Cell[] =>
  (['rules', 'greedy', 'lp'] as PolicyId[]).map((p) => cell(scenario, p)).filter((c): c is Cell => c?.unmet_liters != null);

export interface Verdict {
  /** Every allocating policy that left the least unmet. More than one means a tie. */
  best: Cell[];
  tie: boolean;
  all: boolean;
}

/** Judged on unmet litres, to the whole litre that is shown. */
export function verdict(scenario: string): Verdict | null {
  const cells = allocating(scenario);
  if (cells.length === 0) return null;
  const least = Math.min(...cells.map((c) => Math.round(c.unmet_liters as number)));
  const best = cells.filter((c) => Math.round(c.unmet_liters as number) === least);
  return { best, tie: best.length > 1, all: best.length === cells.length };
}

export const list = (names: string[]) => (names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
export const whole = (n: number | null) => (n == null ? 'n/a' : int.format(Math.round(n)));
export const litres = (n: number | null) => (n == null ? 'n/a' : `${whole(n)} L`);
/** Three decimals, as the benchmark prints it. */
export const level = (n: number | null) => (n == null ? 'n/a' : n.toFixed(3));
export const decimal = (n: number | null) => (n == null ? 'n/a' : n.toLocaleString('en-US', { maximumFractionDigits: 2 }));
/** Fixed UTC text, so the server and the browser print the same thing. */
export const stamp = (iso: string) => `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;

/** A round axis end at or above `max`: 1, 2, 2.5 or 5 times a power of ten. */
export function axisEnd(max: number): number {
  if (!(max > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  return [1, 2, 2.5, 5, 10].map((m) => m * power).find((v) => v >= max) as number;
}
