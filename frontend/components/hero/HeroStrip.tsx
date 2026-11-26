'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Level, Proposal, Risk } from '@/lib/api';
import { hours, int, liters, pct, pretty, tickClock } from '@/lib/format';
import { LEVELS, pairKey, rankRisks } from '@/lib/risk';
import { useLive } from '@/lib/world';
import { FuelTag } from '@/components/ui/Fuel';
import { LevelChip, LevelGlyph, levelLabel } from '@/components/ui/Level';
import { buildCauses } from './causes';
import s from './HeroStrip.module.css';

const OPEN_STATUSES = ['PROPOSED', 'HELD'];
const MAX_CAUSES = 2;

/** Wraps a figure and replays the global `.flash` only when its value changes after first paint. Never flashes while stale. */
function Flash({ value, enabled, className, children }: { value: string; enabled: boolean; className?: string; children: React.ReactNode }) {
  const prev = useRef(value);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (prev.current === value) return;
    prev.current = value;
    if (enabled) setN((c) => c + 1);
  }, [value, enabled]);
  return (
    <span key={n} className={`${className ?? ''}${n > 0 ? ' flash' : ''}`.trim() || undefined}>
      {children}
    </span>
  );
}

/** The big figure: value and unit split so the unit can be set small. `now` means the pair is already at zero. */
function figure(h: number | null): { value: string; unit: string; now: boolean } {
  if (h != null && h <= 0) return { value: 'Stockout now', unit: '', now: true };
  if (h == null || h >= 24) return { value: '24+', unit: 'h', now: false };
  if (h < 1) return { value: String(Math.max(1, Math.round(h * 60))), unit: 'min', now: false };
  return { value: h < 10 ? h.toFixed(1) : String(Math.round(h)), unit: 'h', now: false };
}

const cautious = (h: number | null) => (h != null && h <= 0 ? 'now' : hours(h));

/** Highest-priority open proposal: the one for the pair that ranks worst, then the larger delivery. */
function nextProposal(proposals: Proposal[], ranked: Risk[]): Proposal | null {
  const order = new Map(ranked.map((r, i) => [pairKey(r.station_id, r.fuel_type), i]));
  const open = proposals.filter((p) => OPEN_STATUSES.includes(p.status));
  open.sort(
    (a, b) =>
      (order.get(pairKey(a.station_id, a.fuel_type)) ?? 1e9) - (order.get(pairKey(b.station_id, b.fuel_type)) ?? 1e9) ||
      b.explanation.action.total_liters - a.explanation.action.total_liters ||
      b.id - a.id,
  );
  return open[0] ?? null;
}

/** Brings the recommended proposal's card into view and focuses it, which also opens it if it is folded. */
function showProposal(id: number) {
  const el = document.getElementById(`proposal-${id}`) ?? document.getElementById('proposal-queue');
  if (!el) return;
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  el.focus({ preventScroll: true });
}

function Skeleton() {
  return (
    <section className={s.strip} aria-busy="true" aria-label="Network summary, loading">
      <div className={`${s.hero} ${s.skel}`}>
        <span className="label">Worst pair, time to stockout</span>
        <span className={s.bar} style={{ width: '58%', height: 18 }} />
        <span className={s.bar} style={{ width: '46%', height: 56 }} />
        <span className={s.bar} style={{ width: '82%', height: 14 }} />
      </div>
      <div className={s.right}>
        <div className={`${s.cell} ${s.wide} ${s.skel}`}>
          <span className="label">Pairs by level</span>
          <span className={s.bar} style={{ width: '100%', height: 36 }} />
        </div>
        <div className={`${s.cell} ${s.skel}`}>
          <span className="label">Stations affected</span>
          <span className={s.bar} style={{ width: '40%', height: 28 }} />
        </div>
        <div className={`${s.cell} ${s.skel}`}>
          <span className="label">Simulator, cumulative</span>
          <span className={s.bar} style={{ width: '90%', height: 44 }} />
        </div>
        <div className={`${s.cell} ${s.listCell} ${s.skel}`}>
          <span className="label">Active disruptions</span>
          <span className={s.bar} style={{ width: '80%', height: 14 }} />
          <span className={s.bar} style={{ width: '60%', height: 14 }} />
        </div>
        <div className={`${s.cell} ${s.listCell} ${s.skel}`}>
          <span className="label">Next recommended action</span>
          <span className={s.bar} style={{ width: '90%', height: 14 }} />
          <span className={s.bar} style={{ width: '50%', height: 14 }} />
        </div>
      </div>
      <span className="sr-only">Loading network summary</span>
    </section>
  );
}

export default function HeroStrip() {
  const { status, risks, proposals, snapshot, select } = useLive();
  const stale = status?.mode === 'STALE';
  const live = !stale;

  const ranked = useMemo(() => rankRisks(risks), [risks]);
  const worst = ranked[0] ?? null;
  const world = snapshot?.world ?? null;
  const tick = status?.tick ?? snapshot?.meta.tick ?? 0;
  const causes = useMemo(() => (world ? buildCauses(world, tick) : []), [world, tick]);
  const next = useMemo(() => nextProposal(proposals, ranked), [proposals, ranked]);

  if (!status) return <Skeleton />;

  const total = risks.length;
  const counts = Object.fromEntries(LEVELS.map((l) => [l, risks.filter((r) => r.level === l).length])) as Record<Level, number>;
  const stationTotal = world?.stations.length ?? new Set(risks.map((r) => r.station_id)).size;
  const hot = new Set(risks.filter((r) => r.level === 'CRITICAL' || r.level === 'WARNING').map((r) => r.station_id));
  const m = world?.metrics ?? null;

  const hero = worst ? figure(worst.hours_p50) : null;
  const heroKey = worst ? `${pairKey(worst.station_id, worst.fuel_type)}|${hero?.value}${hero?.unit}` : 'none';
  const stationName = worst ? (world?.stations.find((x) => x.id === worst.station_id)?.name ?? pretty(worst.station_id)) : '';
  const tm = status.tick_minutes;
  const stockoutAt =
    worst && worst.hours_p50 != null && worst.hours_p50 > 0 && worst.hours_p50 < 24 && world
      ? tickClock(world.instance.sim_time, tick, tick + Math.round((worst.hours_p50 * 60) / tm), tm)
      : null;
  const fill = worst && worst.capacity > 0 ? worst.inventory / worst.capacity : null;
  const shown = causes.slice(0, MAX_CAUSES);
  const more = causes.length - shown.length;
  const nextLiters = next?.explanation.action.total_liters ?? 0;

  return (
    <section className={s.strip} data-stale={stale ? 'true' : undefined} aria-label="Network summary">
      <div className={`${s.hero} dimmable`}>
        <span className="label">{worst && worst.level === 'OK' ? 'Tightest cover on the network' : 'Worst pair, time to stockout'}</span>
        {worst && hero ? (
          <>
            <div className={s.who}>
              <span className={s.station}>{stationName}</span>
              <FuelTag fuel={worst.fuel_type} />
              <LevelChip level={worst.level} solid />
            </div>
            <Flash value={heroKey} enabled={live} className={s.figWrap}>
              <span className={`${s.figure}${hero.now ? ` ${s.now}` : ''}`}>
                {hero.value}
                {hero.unit && <span className={s.figUnit}>{hero.unit}</span>}
              </span>
            </Flash>
            <p className={s.caption}>
              {hero.now ? 'This pair is at zero stock.' : <>Expected time to stockout{stockoutAt ? <>, about <span className="mono">{stockoutAt}</span> sim time</> : null}.</>}
            </p>
            <dl className={s.facts}>
              <div>
                <dt>Cautious (P90)</dt>
                <dd className="mono">
                  <Flash value={cautious(worst.hours_p90)} enabled={live}>{cautious(worst.hours_p90)}</Flash>
                </dd>
                <dd className={s.note}>if demand runs high</dd>
              </div>
              <div>
                <dt>On hand</dt>
                <dd className="mono">{liters(worst.inventory)}</dd>
                <dd className={s.note}>{fill == null ? 'capacity unknown' : `${pct(fill, 0)} of ${liters(worst.capacity)}`}</dd>
              </div>
              <div>
                <dt>Inbound</dt>
                <dd className="mono">{liters(worst.inbound)}</dd>
                <dd className={s.note}>{worst.inbound > 0 ? 'already on the road' : 'nothing en route'}</dd>
              </div>
            </dl>
            <button type="button" className={`btn sm ghost ${s.ghostBtn}`} onClick={() => select(pairKey(worst.station_id, worst.fuel_type))}>
              Open pair detail
            </button>
          </>
        ) : (
          <p className={s.empty}>No station-fuel pairs reported yet.</p>
        )}
      </div>

      <div className={s.right}>
        <div className={`${s.cell} ${s.wide} dimmable`}>
          <span className="label">Pairs by level, of {int(total)}</span>
          <ul className={s.levels}>
            {LEVELS.map((l) => (
              <li key={l} className={counts[l] === 0 && l !== 'OK' ? s.zero : undefined}>
                <span className={s.levelName}>
                  <LevelGlyph level={l} size={14} />
                  {levelLabel(l)}
                </span>
                <Flash value={String(counts[l])} enabled={live} className={s.count}>
                  {int(counts[l])}
                </Flash>
              </li>
            ))}
          </ul>
        </div>

        <div className={`${s.cell} dimmable`}>
          <span className="label">Stations affected</span>
          <p className={s.stationFig}>
            <Flash value={String(hot.size)} enabled={live} className={s.count}>{int(hot.size)}</Flash>
            <span className={s.of}> of {int(stationTotal)}</span>
          </p>
          <p className={s.note}>with a pair at warning or worse</p>
        </div>

        <div className={`${s.cell} dimmable`}>
          <span className="label">Simulator, cumulative</span>
          {m ? (
            <dl className={s.metrics}>
              <div>
                <dt>Service level</dt>
                <dd className="mono"><Flash value={pct(m.service_level)} enabled={live}>{pct(m.service_level)}</Flash></dd>
              </div>
              <div>
                <dt>Served</dt>
                <dd className="mono"><Flash value={liters(m.served_demand_liters)} enabled={live}>{liters(m.served_demand_liters)}</Flash></dd>
              </div>
              <div>
                <dt>Unmet</dt>
                <dd className="mono"><Flash value={liters(m.unmet_demand_liters)} enabled={live}>{liters(m.unmet_demand_liters)}</Flash></dd>
              </div>
              <div>
                <dt>Shipped</dt>
                <dd className="mono"><Flash value={liters(m.allocation_liters)} enabled={live}>{liters(m.allocation_liters)}</Flash></dd>
              </div>
            </dl>
          ) : (
            <p className={s.note}>Simulator metrics not in the snapshot yet.</p>
          )}
        </div>

        <div className={`${s.cell} ${s.listCell} dimmable`}>
          <span className="label">Active disruptions</span>
          {!world ? (
            <p className={s.note}>Waiting for the simulator snapshot.</p>
          ) : causes.length === 0 ? (
            <p className={s.calm}>No active disruptions</p>
          ) : (
            <ul className={s.causes}>
              {shown.map((c) => (
                <li key={c.key}>
                  <LevelGlyph level={c.tone} size={14} />
                  <span className={s.causeText}>
                    <span className={s.causeTitle}>
                      {c.title}, {c.pairs} {c.pairs === 1 ? 'pair' : 'pairs'}
                    </span>
                    <span className={`${s.note} mono`}>{c.meta}</span>
                  </span>
                </li>
              ))}
              {more > 0 && <li className={s.more}>+{more} more, {causes.slice(MAX_CAUSES).reduce((a, c) => a + c.pairs, 0)} pairs</li>}
            </ul>
          )}
        </div>

        <div className={`${s.cell} ${s.listCell} dimmable`}>
          <span className="label">Next recommended action</span>
          {next ? (
            <div className={s.action}>
              <p className={s.headline} title={next.explanation.headline.text}>
                {next.explanation.headline.text}
              </p>
              <div className={s.actionRow}>
                <span className="mono">{liters(nextLiters)}</span>
                <span className={s.note}>proposal {next.id}</span>
                <button
                  type="button"
                  className="btn primary sm"
                  onClick={() => {
                    // An open pair detail sheet would cover the queue. Wait for it to close and hand focus back first.
                    select(null);
                    requestAnimationFrame(() => requestAnimationFrame(() => showProposal(next.id)));
                  }}
                >
                  Review
                </button>
              </div>
            </div>
          ) : (
            <p className={s.calm}>No open proposals to review</p>
          )}
        </div>
      </div>
    </section>
  );
}
