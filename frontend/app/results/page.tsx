import { Panel } from '@/components/ui/Panel';
import UnmetBars, { BarRow } from '@/components/results/UnmetBars';
import {
  POLICY_NAME, POLICY_WHAT, allocating, axisEnd, bench, cell, decimal, level, list, litres, stamp, verdict, whole,
} from '@/components/results/data';
import s from '@/components/results/Results.module.css';

const stress = bench.scenarios.filter((x) => x.kind === 'stress' && allocating(x.id).length > 0);
const standard = bench.scenarios.filter((x) => x.kind === 'simulator');
const measured = [...standard, ...stress];

/** One shared axis per panel, set by the largest figure drawn in it. */
const stressEnd = axisEnd(Math.max(...stress.flatMap((x) => allocating(x.id).map((c) => c.unmet_liters ?? 0))));
const allEnd = axisEnd(Math.max(...measured.map((x) => cell(x.id, 'none')?.unmet_liters ?? 0)));

/** What the bars show, in one sentence, worked out from the figures. */
function sentence(id: string): string {
  const v = verdict(id);
  if (!v) return 'No allocating policy was measured here.';
  const names = list(v.best.map((c) => POLICY_NAME[c.policy]));
  const least = v.best[0].unmet_liters ?? 0;
  const amount = Math.round(least) === 0 ? 'nothing unmet' : `${litres(least)} unmet`;
  if (v.tie && v.all) return `Tie: ${names} each leave ${amount}.`;
  if (v.tie) return `Tie: ${names} leave the least, ${amount}.`;
  const next = Math.min(...allocating(id).filter((c) => c !== v.best[0]).map((c) => c.unmet_liters ?? Infinity));
  // the gap between the two figures as shown, so a reader who subtracts them gets the same number
  const gap = Math.round(next) - Math.round(least);
  return Number.isFinite(next) ? `${names} leaves the least, ${amount}: ${litres(gap)} less than the next best.` : `${names} leaves ${amount}.`;
}

const ties = stress.filter((x) => verdict(x.id)?.tie === true);
/** Worlds with a single best policy, grouped by that policy. */
const wins = new Map<string, string[]>();
for (const x of stress) {
  const v = verdict(x.id);
  if (v && !v.tie) wins.set(POLICY_NAME[v.best[0].policy], [...(wins.get(POLICY_NAME[v.best[0].policy]) ?? []), x.id]);
}
const won = wins.size === 0 ? 'no world has a single best policy' : list([...wins].map(([name, ids]) => `${name} alone does best in ${list(ids)}`));
const times = (policy: 'greedy' | 'lp') => bench.cells.filter((c) => c.policy === policy && c.policy_p95_ms != null).map((c) => c.policy_p95_ms as number);
const range = (values: number[]) => (values.length ? `${Math.round(Math.min(...values))} to ${Math.round(Math.max(...values))} ms` : 'not recorded');
const trouble = bench.cells.filter((c) => c.posts_rejected > 0 || (c.failed_allocations ?? 0) > 0 || (c.litres_lost_at_arrival ?? 0) > 0);
const reruns = bench.cells.flatMap((c) => c.superseded.map((older) => ({ cell: c, older })));
const identical = reruns.filter((r) => r.older.same_history_hash && r.older.same_service_level);

/** The measured comparison of allocation policies. Static: it changes only when the saved benchmark runs change. */
export default function ResultsPage() {
  return (
    <div className={s.page}>
      <h1 className="sr-only">Results: how the allocation policies compare</h1>

      <Panel title="What was measured" meta={`seed ${bench.seed ?? 'mixed'} · ${bench.horizon_ticks ?? 'mixed'} ticks`}>
        <p className={s.lede}>
          The simulator is deterministic, so each policy was given the same replayed world, one policy at a time, for {bench.horizon_ticks} ticks (
          {((bench.horizon_ticks ?? 0) * 15) / 60} simulated hours). <strong>Unmet demand</strong> is fuel customers asked for and did not get: lower
          is better. <strong>Service level</strong> is the simulator&rsquo;s own score: 1.000 means all demand was served.
        </p>
        <dl className={s.policies}>
          {bench.policies.map((p) => (
            <div key={p}>
              <dt>{POLICY_NAME[p]}</dt>
              <dd>{POLICY_WHAT[p]}</dd>
            </div>
          ))}
        </dl>
        <p className={s.finding}>
          In the {stress.length} harder worlds, {won}
          {ties.length > 0 && `; ${list(ties.map((x) => x.id))} ${ties.length === 1 ? 'is a tie' : 'are ties'}`}. The price is time: at the 95th
          percentile lp takes {range(times('lp'))} to plan one tick, greedy {range(times('greedy'))}.
        </p>
      </Panel>

      <Panel title="Harder worlds: allocating policies" meta="one scale for all four">
        <p className={s.note}>
          These worlds are our own, built to be hard enough to separate the policies. Doing nothing is not drawn here: it is so much larger that the
          other bars would disappear. It is in the next panel, and under each world.
        </p>
        <div className={s.multiples}>
          {stress.map((x) => {
            const v = verdict(x.id);
            const none = cell(x.id, 'none');
            const rows: BarRow[] = allocating(x.id).map((c) => ({
              key: c.policy,
              name: POLICY_NAME[c.policy],
              cell: c,
              tone: 'policy',
              tag: v?.best.includes(c) ? (v.tie ? 'tie' : 'best') : undefined,
            }));
            return (
              <section key={x.id} className={s.world} aria-labelledby={`w-${x.id}`}>
                <h3 id={`w-${x.id}`} className="mono">{x.id}</h3>
                <p className={s.what}>{x.what}</p>
                <p className={s.verdict}>{sentence(x.id)}</p>
                <UnmetBars rows={rows} end={stressEnd} caption={`${x.id}: unmet demand and service level by policy`} />
                {none && <p className={s.what}>Doing nothing: {litres(none.unmet_liters)} unmet, service level {level(none.service_level)}.</p>}
              </section>
            );
          })}
        </div>
      </Panel>

      <Panel title="Against doing nothing" meta="one scale for every scenario">
        <p className={s.note}>
          The first {standard.length} are the simulator&rsquo;s own scenarios. They are gentle (depots start with plenty of stock), so they show what
          allocating is worth, not which allocator is better. A row with no bar left nothing, or less than half a litre, unmet.
        </p>
        <div className={s.multiples}>
          {measured.map((x) => {
            const v = verdict(x.id);
            const none = cell(x.id, 'none');
            if (!v || !none) return null;
            const missing = bench.policies.filter((p) => !cell(x.id, p));
            const rows: BarRow[] = [
              { key: 'none', name: POLICY_NAME.none, cell: none, tone: 'nothing' },
              { key: 'best', name: list(v.best.map((c) => POLICY_NAME[c.policy])), cell: v.best[0], tone: 'policy', tag: v.tie ? 'tie' : 'best' },
            ];
            return (
              <section key={x.id} className={s.world} aria-labelledby={`a-${x.id}`}>
                <h3 id={`a-${x.id}`} className="mono">{x.id}</h3>
                <p className={s.what}>
                  {x.what}
                  {missing.length > 0 && ` Not run here: ${list(missing.map((p) => POLICY_NAME[p]))}.`}
                </p>
                <UnmetBars rows={rows} end={allEnd} caption={`${x.id}: doing nothing against the best policy`} />
              </section>
            );
          })}
        </div>
      </Panel>

      <Panel title="Every measured figure" meta={`${bench.cells.length} runs of a policy on a scenario`} flush>
        <div className={s.scroll} tabIndex={0} role="region" aria-label="Every measured figure, scrolls sideways">
          <table className={`tbl ${s.full}`}>
            <thead>
              <tr>
                <th scope="col">
                  Scenario<span className={s.under}> and policy</span>
                </th>
                <th scope="col" className={s.policyCol}>Policy</th>
                <th scope="col" className="r">Service level</th>
                <th scope="col" className="r">Unmet L</th>
                <th scope="col" className="r">Shipments</th>
                <th scope="col" className="r">Litres shipped</th>
                <th scope="col" className="r">Rejected sends</th>
                <th scope="col" className="r">Failed shipments</th>
                <th scope="col" className="r">Lost at arrival L</th>
                <th scope="col" className="r">Stockout pair-hours</th>
                <th scope="col" className="r">Policy time p95 ms</th>
                <th scope="col">Saved run</th>
              </tr>
            </thead>
            <tbody>
              {bench.cells.map((c) => (
                <tr key={`${c.scenario}|${c.policy}`} data-cell={`${c.scenario}|${c.policy}`}>
                  <th scope="row">
                    <span className="mono">{c.scenario}</span>
                    {/* on a narrow screen the policy column scrolls away, so the name sits in the fixed column instead */}
                    <span className={s.under}>{POLICY_NAME[c.policy]}</span>
                  </th>
                  <td className={s.policyCol}>{POLICY_NAME[c.policy]}</td>
                  <td className="num">{level(c.service_level)}</td>
                  <td className="num">{whole(c.unmet_liters)}</td>
                  <td className="num">{whole(c.shipments)}</td>
                  <td className="num">{whole(c.litres_shipped)}</td>
                  <td className="num">{whole(c.posts_rejected)}</td>
                  <td className="num">{whole(c.failed_allocations)}</td>
                  <td className="num">{whole(c.litres_lost_at_arrival)}</td>
                  <td className="num">{decimal(c.stockout_pair_hours)}</td>
                  <td className="num">{c.policy_p95_ms == null ? <span className="faint">not recorded</span> : decimal(c.policy_p95_ms)}</td>
                  <td className="mono">{c.source_run}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={s.footnote}>
          Policy time is the policy&rsquo;s own planning step. A run that timed only the whole decision, a different measure, shows
          &ldquo;not recorded&rdquo;.
        </p>
      </Panel>

      <Panel title="How far to trust this">
        <ul className={s.limits}>
          <li>
            <strong>One seed, and one run behind each figure.</strong> There are no confidence intervals, and a different seed could change the small
            gaps.
          </li>
          <li>
            <strong>No injected faults.</strong> The benchmark says nothing about how the policies behave when the simulator is slow, wrong or gone.
          </li>
          <li>
            <strong>The harder worlds are our own.</strong> They were chosen to be hard; they are not the simulator&rsquo;s scenario files.
          </li>
          <li>
            <strong>The lp was not tuned.</strong> Its weights were set by reasoning, and only its P80 demand path was measured.
          </li>
          {trouble.length > 0 && (
            <li>
              <strong>Sends that went wrong:</strong>{' '}
              {list(
                trouble.map((c) => {
                  const what = [
                    c.posts_rejected > 0 && `${whole(c.posts_rejected)} rejected sends`,
                    (c.failed_allocations ?? 0) > 0 && `${whole(c.failed_allocations)} failed shipments`,
                    (c.litres_lost_at_arrival ?? 0) > 0 && `${litres(c.litres_lost_at_arrival)} lost at arrival`,
                  ].filter(Boolean) as string[];
                  return `${POLICY_NAME[c.policy]} in ${c.scenario} (${list(what)})`;
                }),
              )}
              . No other run had a rejected send, a failed shipment or fuel lost at arrival.
            </li>
          )}
          {reruns.length > 0 && (
            <li>
              <strong>Repeats:</strong> {reruns.length} cells were run again in a later saved run; {identical.length} of them gave an identical demand
              history and service level. Where a cell has two runs, the newer one is shown.
            </li>
          )}
        </ul>
        <h3 className={s.subhead}>Saved runs</h3>
        <div className={s.scroll} tabIndex={0} role="region" aria-label="Saved runs, scrolls sideways">
          <table className={`tbl ${s.runs}`}>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Finished</th>
                <th scope="col">Code at commit</th>
                <th scope="col">Policies</th>
                <th scope="col" className="r">Cells</th>
              </tr>
            </thead>
            <tbody>
              {bench.runs.map((r) => (
                <tr key={r.id}>
                  <th scope="row" className="mono">bench/results/{r.id}</th>
                  <td className="mono">{stamp(r.generated_at)}</td>
                  <td className="mono">{r.git_commit}</td>
                  <td>{list(r.policies.map((p) => p))}</td>
                  <td className="num">{r.cells}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={s.note}>
          This page is built from those files by <code className={s.code}>npm run bench:data</code>, run in{' '}
          <code className={s.code}>frontend/</code>. It does not change while the console runs.
        </p>
      </Panel>
    </div>
  );
}
