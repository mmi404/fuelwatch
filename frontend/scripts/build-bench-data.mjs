#!/usr/bin/env node
// Merges the saved replay-benchmark runs (bench/results/*/results.json) into data/bench.json.
// No dependencies. Run from frontend/:  node scripts/build-bench-data.mjs
//
// Rule: where the same scenario x policy appears in more than one run, the newest run wins
// (newest = latest generated_at). The choice is recorded on the cell in `superseded`.
// Nothing is estimated: a number the source run did not record stays null.

import { readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const resultsDir = join(root, 'bench', 'results');
const outFile = resolve(here, '..', 'data', 'bench.json');

const POLICIES = ['none', 'rules', 'greedy', 'lp'];

// Scenario order and one-line descriptions. Stress worlds: docs/design/06-algorithms.md section 6.8.
// The four simulator scenarios are the simulator's own event files, replayed through /admin/events.
// Each description follows the events in backend/fuelwatch/bench.py; check it there if an event changes.
const SCENARIOS = [
  { id: 'baseline', kind: 'simulator', what: 'The simulator baseline: no injected events.' },
  { id: 'demand_spike', kind: 'simulator', what: 'Demand x1.8 in Dhaka for 12 ticks.' },
  { id: 'supply_disruption', kind: 'simulator', what: 'Gazipur diesel deliveries delayed 8 ticks, and supply to Patiya cut to 60%.' },
  { id: 'final_combined', kind: 'simulator', what: 'Demand x1.7 in Dhaka for 16 ticks, Gazipur diesel and petrol delayed 8 ticks, and the Gazipur to Mirpur route closed for 10 ticks.' },
  { id: 'stress_crunch', kind: 'stress', what: 'Demand x3 everywhere for 80 ticks, all supply cut to 20%, deliveries delayed 10 ticks.' },
  { id: 'stress_roads', kind: 'stress', what: 'Two routes down for 40 ticks, a third for 20, demand x2.2.' },
  { id: 'stress_outage_rebound', kind: 'stress', what: 'All supply cut to 30% early on, two stations closed for 12 ticks, then demand x2.5.' },
  { id: 'stress_everything', kind: 'stress', what: 'Demand x2.6, supply cut to 25%, deliveries delayed 12 ticks, two routes down for 30 ticks, one station closed for 8.' },
];

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const sum = (o) => (o && typeof o === 'object' ? Object.values(o).reduce((a, b) => a + (Number(b) || 0), 0) : 0);

if (!existsSync(resultsDir)) {
  console.error(`No results folder at ${resultsDir}`);
  process.exit(1);
}

const runs = [];
for (const id of readdirSync(resultsDir).sort()) {
  const jsonPath = join(resultsDir, id, 'results.json');
  if (!existsSync(jsonPath)) continue;
  const j = JSON.parse(readFileSync(jsonPath, 'utf8'));
  // Seed is only printed in results.md ("Commit X, horizon N ticks, simulator seed S").
  let seed = null;
  const mdPath = join(resultsDir, id, 'results.md');
  if (existsSync(mdPath)) {
    const m = /simulator seed (\d+)/.exec(readFileSync(mdPath, 'utf8'));
    if (m) seed = Number(m[1]);
  }
  runs.push({
    id,
    generated_at: j.generated_at,
    git_commit: j.git_commit,
    simulator: j.simulator,
    horizon_ticks: j.horizon_ticks,
    seed,
    reproducibility: j.reproducibility ?? {},
    rows: j.results,
  });
}
if (runs.length === 0) {
  console.error('No results.json files found.');
  process.exit(1);
}
runs.sort((a, b) => (a.generated_at < b.generated_at ? -1 : a.generated_at > b.generated_at ? 1 : 0));

function toCell(run, r) {
  return {
    scenario: r.scenario,
    policy: r.policy,
    service_level: num(r.service_level),
    unmet_liters: num(r.unmet_liters),
    served_liters: num(r.served_liters),
    shipments: num(r.shipments_posted),
    litres_shipped: num(r.litres_shipped),
    posts_rejected: sum(r.posts_rejected),
    failed_allocations: num(r.simulator_allocation_failures),
    litres_lost_at_arrival: num(r.litres_lost_at_arrival),
    stockout_pair_hours: num(r.stockout_pair_hours),
    // Older runs recorded only whole-decision time, not policy time: left null, never copied across.
    policy_p95_ms: num(r.policy_ms_p95),
    fallbacks: num(r.policy_fallbacks),
    history_hash: r.history_hash ?? null,
    source_run: run.id,
    git_commit: run.git_commit,
    generated_at: run.generated_at,
    superseded: [],
  };
}

// Oldest first, so a later run overwrites an earlier one and the older one is recorded.
const byKey = new Map();
for (const run of runs) {
  for (const r of run.rows) {
    const key = `${r.scenario}|${r.policy}`;
    const next = toCell(run, r);
    const prev = byKey.get(key);
    if (prev) {
      next.superseded = [
        ...prev.superseded,
        {
          source_run: prev.source_run,
          git_commit: prev.git_commit,
          service_level: prev.service_level,
          unmet_liters: prev.unmet_liters,
          policy_p95_ms: prev.policy_p95_ms,
          same_history_hash: prev.history_hash === next.history_hash,
          same_service_level: prev.service_level === next.service_level,
        },
      ];
    }
    byKey.set(key, next);
  }
}

const known = new Set(SCENARIOS.map((s) => s.id));
for (const c of byKey.values()) {
  if (!known.has(c.scenario)) console.warn(`Scenario ${c.scenario} is not in the SCENARIOS list; skipped.`);
}
const cells = [];
for (const s of SCENARIOS) for (const p of POLICIES) if (byKey.has(`${s.id}|${p}`)) cells.push(byKey.get(`${s.id}|${p}`));

const horizons = [...new Set(runs.map((r) => r.horizon_ticks))];
const seeds = [...new Set(runs.map((r) => r.seed).filter((s) => s !== null))];
const reproducibility = [];
for (const run of runs) for (const [k, v] of Object.entries(run.reproducibility)) reproducibility.push({ run: run.id, key: k, reproduced: v === true });

const doc = {
  note: 'Generated by scripts/build-bench-data.mjs from bench/results/*/results.json. Do not edit by hand.',
  horizon_ticks: horizons.length === 1 ? horizons[0] : null,
  seed: seeds.length === 1 ? seeds[0] : null,
  simulator: [...new Set(runs.map((r) => r.simulator))].join(', '),
  policies: POLICIES,
  scenarios: SCENARIOS,
  runs: runs.map((r) => ({
    id: r.id,
    generated_at: r.generated_at,
    git_commit: r.git_commit,
    cells: r.rows.length,
    scenarios: [...new Set(r.rows.map((x) => x.scenario))],
    policies: [...new Set(r.rows.map((x) => x.policy))],
  })),
  merge_rule: 'Same scenario x policy in more than one run: the newest run (latest generated_at) is used; the older figures are kept in each cell under "superseded".',
  reproducibility,
  cells,
};

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, JSON.stringify(doc, null, 2) + '\n');
const dup = cells.filter((c) => c.superseded.length > 0);
console.log(`Wrote ${outFile}: ${cells.length} cells from ${runs.length} runs; ${dup.length} cells took the newest of several runs.`);
for (const c of dup) console.log(`  ${c.scenario}/${c.policy}: using ${c.source_run} over ${c.superseded.map((s) => `${s.source_run} (same hash: ${s.same_history_hash}, same service level: ${s.same_service_level})`).join(', ')}`);
