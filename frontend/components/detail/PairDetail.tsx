'use client';

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { FuelTag } from '@/components/ui/Fuel';
import { LevelChip, LevelGlyph } from '@/components/ui/Level';
import type { Proposal, Risk } from '@/lib/api';
import { age, fuelName, hours, int, liters, pct, pretty, tickClock } from '@/lib/format';
import { routeClosed, splitKey } from '@/lib/risk';
import { useLive } from '@/lib/world';
import { COVER_TICKS, FUTURE_TICKS, Inbound, inboundFor, project } from './model';
import StockChart, { CHART_HEIGHT, MIN_OBSERVED } from './StockChart';
import { useForecast } from './useForecast';
import css from './PairDetail.module.css';

const DESKTOP = '(min-width: 1100px)';
const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The sheet for the selected station-fuel pair. Esc is handled by the shell, which clears the selection. */
export default function PairDetail() {
  const { selected } = useLive();
  return selected ? <Sheet pair={selected} /> : null;
}

function useDesktop(): boolean {
  // Only ever mounted after a client-side selection, so matchMedia is safe on the first render.
  const [desktop, setDesktop] = useState(() => typeof window === 'undefined' || window.matchMedia(DESKTOP).matches);
  useEffect(() => {
    const m = window.matchMedia(DESKTOP);
    const sync = () => setDesktop(m.matches);
    sync();
    m.addEventListener('change', sync);
    return () => m.removeEventListener('change', sync);
  }, []);
  return desktop;
}

const usable = (el: Element | null): el is HTMLElement => el instanceof HTMLElement && el.isConnected && el !== document.body && !el.closest('dialog') && el.getClientRects().length > 0;

/** Hours of cover in words. The backend sends null when it forecasts no stockout within its 96-tick (24 h) horizon (stockout.py). */
const cover = (h: number | null | undefined) => (h == null ? 'more than 24 h' : h === 0 ? 'now' : hours(h, Infinity));

function Sheet({ pair }: { pair: string }) {
  const { status, risks, proposals, alerts, snapshot, history, select } = useLive();
  const desktop = useDesktop();
  const titleId = useId();
  const sheet = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const keepFocus = useRef(false);
  const lastPair = useRef(pair);
  lastPair.current = pair;

  // Take focus on open; give it back to the opener (or the pair's row) when the sheet goes away.
  useEffect(() => {
    const before = document.activeElement;
    if (before instanceof HTMLElement && !sheet.current?.contains(before)) opener.current = before;
    sheet.current?.focus({ preventScroll: true });
    return () => {
      if (keepFocus.current) return;
      const now = document.activeElement;
      // Focus is only "lost" if it was on something that left the page with the sheet. Never pull it off the board.
      if (now && now !== document.body && now.isConnected) return;
      if (usable(opener.current)) opener.current.focus();
      else {
        const row = document.querySelector(`[data-pair="${lastPair.current}"]`);
        if (usable(row)) row.focus();
      }
    };
  }, []);

  // The drawer is modal: the page behind it must not scroll.
  useEffect(() => {
    if (desktop) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [desktop]);

  const { stationId, fuel } = splitKey(pair);
  const world = snapshot?.world;
  const station = world?.stations.find((s) => s.id === stationId);
  const risk = risks.find((r) => r.station_id === stationId && r.fuel_type === fuel);

  // A reset or a new scenario can remove the pair: close rather than show an empty shell.
  const gone = !!snapshot && risks.length > 0 && !risk && (!station || !(fuel in station.inventory));
  useEffect(() => {
    if (gone) select(null);
  }, [gone, select]);

  const nowTick = world?.instance.tick ?? null;
  const tickMinutes = world?.instance.tick_minutes ?? status?.tick_minutes ?? 15;
  const simTime = world?.instance.sim_time ?? null;
  const forecast = useForecast(stationId, fuel, nowTick);

  const inventory = risk?.inventory ?? station?.inventory[fuel] ?? null;
  const capacity = risk?.capacity ?? station?.capacity[fuel] ?? null;
  const inbound = useMemo(() => (world && nowTick !== null ? inboundFor(world.allocations, world.routes, stationId, fuel, nowTick) : []), [world, stationId, fuel, nowTick]);
  const projection = useMemo(
    () => (forecast.rows && inventory !== null && capacity !== null && nowTick !== null ? project(inventory, capacity, forecast.rows, nowTick, inbound) : null),
    [forecast.rows, inventory, capacity, nowTick, inbound],
  );
  const observed = history[pair] ?? [];
  const observedCount = nowTick === null ? 0 : new Set([...observed.map((p) => p.tick), nowTick]).size;

  const name = station?.name ?? pretty(stationId);
  const fuelWord = fuelName(fuel).toLowerCase();
  const clock = (t: number) => tickClock(simTime, nowTick ?? t, t, tickMinutes);
  const span = (ticks: number) => {
    const m = ticks * tickMinutes;
    return m < 60 ? `${m} min` : `${+(m / 60).toFixed(2)} h`;
  };

  const open = proposals.filter((p) => p.station_id === stationId && p.fuel_type === fuel && (p.status === 'PROPOSED' || p.status === 'APPROVED')).sort((a, b) => b.id - a.id);
  const pairAlerts = alerts.filter((a) => a.station_id === stationId && a.fuel_type === fuel);
  const routes = (world?.routes ?? []).filter((r) => r.destination_station_id === stationId).sort((a, b) => a.transit_ticks - b.transit_ticks);
  const bestTicks = (risk as (Risk & { best_route_ticks?: number | null }) | undefined)?.best_route_ticks ?? routes.find((r) => !routeClosed(r.status))?.transit_ticks ?? null;
  const accuracy = status?.forecast;

  const [missing, setMissing] = useState<number | null>(null);
  const openProposal = (p: Proposal) => {
    if (!document.getElementById(`proposal-${p.id}`)) {
      setMissing(p.id);
      return;
    }
    // The sheet covers the proposal column, so close it first and send focus to the card instead of the opener.
    keepFocus.current = true;
    select(null);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const card = document.getElementById(`proposal-${p.id}`);
        card?.scrollIntoView({ block: 'start' });
        card?.focus({ preventScroll: true });
      }),
    );
  };

  const trap = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (desktop || e.key !== 'Tab' || !sheet.current) return;
    const items = [...sheet.current.querySelectorAll<HTMLElement | SVGElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const at = document.activeElement;
    if (e.shiftKey && (at === first || at === sheet.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && at === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const stockoutNow = !!risk && (risk.inventory <= 0 || risk.hours_p50 === 0);
  const bigFigure = !risk ? '--' : risk.level === 'UNKNOWN' ? 'no estimate' : risk.inventory <= 0 ? 'empty now' : cover(risk.hours_p50);
  const departedCount = inbound.filter((a) => a.departed).length;

  return (
    <>
      {/* preventDefault keeps focus in the drawer until it closes, so it can go back to the opener */}
      {!desktop && <div className={css.backdrop} aria-hidden="true" onMouseDown={(e) => e.preventDefault()} onClick={() => select(null)} />}
      <div ref={sheet} className={css.sheet} role="dialog" aria-modal={desktop ? 'false' : 'true'} aria-labelledby={titleId} tabIndex={-1} onKeyDown={trap} data-detail={pair}>
        <header className={css.head}>
          <div className={css.titleRow}>
            <h2 id={titleId} className={css.title}>
              {name}
              <span className="sr-only">, {fuelWord}</span>
            </h2>
            <button type="button" className={`btn ghost ${css.close}`} onClick={() => select(null)} aria-label="Close pair detail" title="Close (Esc)">
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
                <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className={css.tags}>
            <FuelTag fuel={fuel} />
            {risk && <LevelChip level={risk.level} solid />}
          </div>
        </header>

        <div className={`${css.body} dimmable`}>
          {status?.mode === 'STALE' && (
            <p className={css.notice} role="status">Stale data: the simulator reading is {age(status.age_seconds)} old. Figures may no longer be true.</p>
          )}

          <section className={css.section} aria-label="Current position">
            <p className={css.hero}>
              <span className={css.heroFig}>{bigFigure}</span>
              <span className="muted">
                {stockoutNow ? 'demand is going unserved' : 'to stockout, expected'}
                {risk && risk.level !== 'UNKNOWN' && !stockoutNow ? `; cautious (P90) ${cover(risk.hours_p90)}` : ''}
              </span>
            </p>
            <dl className={css.facts}>
              <div><dt>Stock now</dt><dd>{liters(inventory)}</dd></div>
              <div><dt>Capacity</dt><dd>{liters(capacity)}</dd></div>
              <div><dt>Fill</dt><dd>{inventory !== null && capacity ? pct(inventory / capacity, 0) : 'n/a'}</dd></div>
              <div><dt>Inbound</dt><dd>{liters(risk?.inbound)}</dd></div>
              <div><dt>Best route</dt><dd>{bestTicks === null ? 'n/a' : span(bestTicks)}</dd></div>
              <div><dt>Station</dt><dd>{station ? station.status.toLowerCase() : 'n/a'}</dd></div>
            </dl>
          </section>

          <section className={css.section}>
            <h3 className="label">Stock, litres, simulated time</h3>
            {forecast.error && (
              <div className={css.error} role="alert">
                <span>
                  Forecast did not load: {forecast.error}. {forecast.rows ? 'Showing the previous forecast.' : 'Showing observed stock only.'}
                </span>
                <button type="button" className="btn sm" onClick={forecast.retry}>Retry forecast</button>
              </div>
            )}
            {inventory === null || capacity === null || nowTick === null || (!forecast.rows && !forecast.error) ? (
              <div className={css.skeleton} style={{ height: CHART_HEIGHT }} aria-busy="true">
                <span className="sr-only">Loading the stock chart</span>
              </div>
            ) : (
              <StockChart
                key={pair}
                name={`${name} ${fuelWord}`}
                nowTick={nowTick}
                tickMinutes={tickMinutes}
                simTime={simTime}
                capacity={capacity}
                inventory={inventory}
                observed={observed}
                projection={projection}
                inbound={inbound}
              />
            )}
            <ul className={css.keys} aria-label="Chart key">
              <li><span className={css.kObs} />observed</li>
              <li><span className={css.kExp} />projected, expected demand</li>
              <li><span className={css.kCore} /><span className={css.kOuter} />cautious band</li>
              {inbound.length > 0 && <li><span className={css.kShip} />shipment arrives</li>}
            </ul>
            <div className={css.caption}>
              <p>
                The band is a cautious bound, <strong>wider than reality</strong>: its lower edge runs demand at P90 in every tick at once, its upper edge at P10 in every tick. The darker core goes half as far from the
                expected line. It is not a guarantee.
              </p>
              <p>
                {observedCount < MIN_OBSERVED ? `Observed history is still collecting (${observedCount} of ${MIN_OBSERVED} readings). ` : `Observed: ${observedCount} readings. `}
                History starts when this page was loaded; no earlier readings are kept. The projection spans up to {span(FUTURE_TICKS)}; cover target is {span(COVER_TICKS)} of expected demand.
              </p>
              <InboundNote inbound={inbound} departed={departedCount} clock={clock} />
              <p>Scheduled supply and station outages are not modelled here.</p>
            </div>
          </section>

          <section className={css.section}>
            <h3 className="label">Why this level</h3>
            {open.length === 0 ? (
              <p className="muted">
                No open proposal for this pair. The level follows the cautious (P90) hours to stockout{risk ? `: ${risk.level === 'UNKNOWN' ? 'no estimate yet' : cover(risk.hours_p90)}` : ''}.
              </p>
            ) : (
              <ul className={css.list}>
                {open.map((p) => (
                  <li key={p.id} className={css.why}>
                    <p>{p.explanation.headline.text}</p>
                    <p className={css.effect}>
                      <span className="muted">Cautious cover if dispatched</span>
                      <span className="mono">{cover(p.explanation.effect.hours_p90_before)} to {cover(p.explanation.effect.hours_p90_after)}</span>
                      {p.explanation.effect.level_after && <LevelChip level={p.explanation.effect.level_after} />}
                    </p>
                    <p className={css.whyFoot}>
                      <span className="muted">
                        Proposal {p.id}, {liters(p.explanation.action.total_liters)}, {p.status === 'PROPOSED' ? 'awaiting a decision' : 'approved'}
                      </span>
                      <button type="button" className="btn sm" onClick={() => openProposal(p)}>Open proposal</button>
                    </p>
                    {missing === p.id && <p role="alert" className={css.errorText}>Proposal {p.id} is not in the queue on this screen.</p>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={css.section}>
            <h3 className="label">Routes to this station</h3>
            {routes.length === 0 ? (
              <p className="muted">{world ? 'No route serves this station.' : 'Loading routes.'}</p>
            ) : (
              <table className={`tbl ${css.table}`}>
                <thead>
                  <tr>
                    <th scope="col">From depot</th>
                    <th scope="col" className="r">Transit</th>
                    <th scope="col" className="r">Max load</th>
                    <th scope="col">Route</th>
                    <th scope="col" className="r">Depot {fuelWord}</th>
                  </tr>
                </thead>
                <tbody>
                  {routes.map((r) => {
                    const depot = world?.depots.find((d) => d.id === r.source_depot_id);
                    return (
                      <tr key={r.id}>
                        <td>
                          {pretty(r.source_depot_id)}
                          {depot && depot.status !== 'OPEN' && (
                            <span className={css.state}><LevelGlyph level="OUTAGE" size={12} />depot {depot.status.toLowerCase()}</span>
                          )}
                        </td>
                        <td className="num">{span(r.transit_ticks)}</td>
                        <td className="num">{int(r.max_shipment)} L</td>
                        <td>
                          {routeClosed(r.status) ? (
                            <span className={css.state}><LevelGlyph level="OUTAGE" size={12} />{r.status.toLowerCase()}</span>
                          ) : (
                            <span className="muted">available</span>
                          )}
                        </td>
                        <td className="num">{depot ? `${int(depot.inventory[fuel])} L` : 'n/a'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>

          <section className={css.section}>
            <h3 className="label">Alerts for this pair</h3>
            {pairAlerts.length === 0 ? (
              <p className="muted">No alerts for this pair.</p>
            ) : (
              <ul className={css.list}>
                {pairAlerts.map((a) => (
                  <li key={a.id} className={css.alert}>
                    <LevelChip level={a.level} />
                    <span>
                      {a.state.toLowerCase()}, since {clock(a.first_tick)} (tick {a.first_tick})
                    </span>
                    <span className="muted">cautious cover {cover(a.hours_p90)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {accuracy && accuracy.coverage_80 !== null && accuracy.mape !== null && (
            <p className={`${css.section} muted`}>
              Forecast accuracy, all pairs, {int(accuracy.forecasts_scored)} scored: actual demand fell inside the P10 to P90 range {pct(accuracy.coverage_80, 0)} of the time (aim 80%); the average miss was{' '}
              {pct(accuracy.mape)}.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function InboundNote({ inbound, departed, clock }: { inbound: Inbound[]; departed: number; clock: (t: number) => string }) {
  if (inbound.length === 0) return <p>No shipment is on its way to this tank, so none is added.</p>;
  const estimated = inbound.some((a) => a.estimated);
  return (
    <p>
      Includes only shipments already committed to this tank: {inbound.map((a) => `${liters(a.liters)} at ${clock(a.tick)}${a.departed ? '' : ' (not departed yet)'}`).join('; ')}.
      {estimated ? ' Where the simulator has not set an arrival, it is order time plus route transit.' : ''}
      {departed === 0 ? ' None has left the depot.' : ''}
    </p>
  );
}
