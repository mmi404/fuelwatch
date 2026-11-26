'use client';

import Link from 'next/link';
import { Fragment, useMemo, useState } from 'react';
import { FuelTag } from '@/components/ui/Fuel';
import { Panel } from '@/components/ui/Panel';
import type { LedgerLine, World } from '@/lib/api';
import { age, int, liters, pct, pretty, tickClock } from '@/lib/format';
import CopyKey from './CopyKey';
import { ALL_STATUSES, STATUS_MEANING, clipInfo, confidenceWord, failureText, lineTick, routeLabel, statusWord } from './model';
import StatusStepper, { StatusGlyph } from './StatusStepper';
import type { LedgerState } from './useLedger';
import s from './Ledger.module.css';

const COLS = 9;

function Skeleton() {
  return (
    <div className={s.tableWrap} aria-busy="true" aria-label="Loading shipment lines">
      <table className={`tbl ${s.table}`}>
        <tbody>
          {Array.from({ length: 6 }, (_, i) => (
            <tr key={i} className={s.skelRow}>
              {[28, 36, 70, 130, 70, 110, 150, 90, 130].map((w, j) => (
                <td key={j}><span className={`${s.skel} pulse`} style={{ width: w, animationDelay: `${i * 80}ms` }} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ line, station, world, routeMax }: { line: LedgerLine; station: string; world: World | null; routeMax: number | undefined }) {
  const sim = line.simulator;
  const nowTick = world?.instance.tick ?? 0;
  const tm = world?.instance.tick_minutes ?? 15;
  const at = (tick: number | null) => (tick == null ? 'n/a' : `tick ${tick} (${tickClock(world?.instance.sim_time, nowTick, tick, tm)})`);
  const fail = failureText(line.failure_code);
  return (
    <div className={s.detail}>
      <p className={s.detailMeaning}>
        <StatusStepper status={line.status} full />
        <span className="muted">{STATUS_MEANING[line.status] ?? ''}</span>
      </p>
      <dl className={s.facts}>
        <div><dt className="label">Proposal</dt><dd><span className="mono">#{line.proposal_id}</span>, {line.proposal_status.toLowerCase()}, policy {line.policy}</dd></div>
        <div><dt className="label">Confidence</dt><dd>{confidenceWord(line.confidence)} ({pct(line.confidence, 0)})</dd></div>
        <div><dt className="label">Based on</dt><dd>snapshot at {at(line.snapshot_tick)}</dd></div>
        <div><dt className="label">Route</dt><dd>{pretty(line.depot_id)} depot to {pretty(station)} station <span className="faint mono">{line.route_id}</span>{routeMax != null && <>, up to {liters(routeMax)} per shipment</>}</dd></div>
        <div><dt className="label">Quantity</dt><dd>proposed {liters(line.proposed_quantity)}, executed {line.executed_quantity == null ? 'not set yet' : liters(line.executed_quantity)}</dd></div>
        {line.failure_code && <div><dt className="label">Failure code</dt><dd><span className="mono">{line.failure_code}</span>{fail && fail !== line.failure_code.toLowerCase().replace(/_/g, ' ') ? `: ${fail}` : ''}</dd></div>}
      </dl>
      <div className={s.simBlock}>
        <h3 className="label">Simulator record</h3>
        {sim ? (
          <dl className={s.facts}>
            <div><dt className="label">Allocation</dt><dd><span className="mono">#{sim.id}</span>, {sim.status.toLowerCase().replace(/_/g, ' ')}</dd></div>
            <div><dt className="label">Created</dt><dd>{at(sim.created_tick)}</dd></div>
            <div><dt className="label">Departed</dt><dd>{at(sim.departure_tick)}</dd></div>
            <div><dt className="label">{sim.actual_arrival_tick != null ? 'Arrived' : 'Expected arrival'}</dt><dd>{at(sim.actual_arrival_tick ?? sim.expected_arrival_tick)}</dd></div>
            {sim.failure_reason && <div><dt className="label">Failure reason</dt><dd>{failureText(sim.failure_reason)} <span className="faint mono">{sim.failure_reason}</span></dd></div>}
          </dl>
        ) : (
          <p className="muted">This line is not in the simulator&apos;s current snapshot. The ledger keeps its own record; it is matched to the simulator by idempotency key on every snapshot.</p>
        )}
      </div>
      <div className={s.simBlock}>
        <h3 className="label">Idempotency key</h3>
        <p className={s.fullKey}><code className="mono">{line.idempotency_key}</code><CopyKey value={line.idempotency_key} label={`line ${line.id}`} buttonOnly /></p>
        <p className="faint">The key is stored before the request is sent. Sending it again returns the same allocation, so a retry can never ship twice. Attempt history is not part of the allocations endpoint; the latest state is shown.</p>
      </div>
    </div>
  );
}

export default function ShipmentTable({ state, world, refresh }: { state: LedgerState; world: World | null; refresh: () => void }) {
  const [wanted, setWanted] = useState<ReadonlySet<string>>(new Set());
  const [station, setStation] = useState('');
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const routes = useMemo(() => world?.routes ?? [], [world]);
  const nowTick = world?.instance.tick ?? 0;
  const tm = world?.instance.tick_minutes ?? 15;
  const simTime = world?.instance.sim_time;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const line of state.lines) c[line.status] = (c[line.status] ?? 0) + 1;
    return c;
  }, [state.lines]);
  const stations = useMemo(() => [...new Set(state.lines.map((l) => l.station_id))].sort(), [state.lines]);
  const shown = state.lines.filter((l) => (wanted.size === 0 || wanted.has(l.status)) && (!station || l.station_id === station));
  const filtering = wanted.size > 0 || station !== '';

  const toggle = (st: string) =>
    setWanted((prev) => {
      const next = new Set(prev);
      if (next.has(st)) next.delete(st);
      else next.add(st);
      return next;
    });
  const flip = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const total = state.lines.length;
  const meta = !state.loaded ? 'loading' : filtering ? `${shown.length} of ${total} lines` : `${total} ${total === 1 ? 'line' : 'lines'}`;
  const actions = (
    <button type="button" className="btn sm ghost" onClick={refresh} aria-label="Refresh shipment lines">Refresh</button>
  );

  let body: React.ReactNode;
  if (!state.loaded && state.error) {
    body = (
      <div className={s.empty} role="alert">
        <p className={s.emptyTitle}>The ledger could not be read.</p>
        <p className="muted">{state.error}. Nothing is lost: lines are stored by the backend. Try again in a moment.</p>
        <button type="button" className="btn sm" onClick={refresh}>Try again</button>
      </div>
    );
  } else if (!state.loaded) {
    body = <Skeleton />;
  } else if (total === 0) {
    body = (
      <div className={s.empty}>
        <p className={s.emptyTitle}>No shipments sent yet.</p>
        <p className="muted">No shipments have been dispatched in this simulation epoch. Approve a proposal on the overview to create the first ledger line.</p>
        <Link className="btn sm" href="/">Go to the overview</Link>
      </div>
    );
  } else if (shown.length === 0) {
    body = (
      <div className={s.empty}>
        <p className={s.emptyTitle}>No lines match these filters.</p>
        <p className="muted">{total} {total === 1 ? 'line is' : 'lines are'} in the ledger, none with {wanted.size ? [...wanted].map((w) => statusWord(w).toLowerCase()).join(' or ') : 'this status'}{station ? ` for ${pretty(station)}` : ''}.</p>
        <button type="button" className="btn sm" onClick={() => { setWanted(new Set()); setStation(''); }}>Clear filters</button>
      </div>
    );
  } else {
    body = (
      <div className={`${s.tableWrap} dimmable`}>
        <table className={`tbl ${s.table}`}>
          <caption className="sr-only">Shipment lines, newest first. Expand a row for the simulator record and the idempotency key.</caption>
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col">Proposal</th>
              <th scope="col">Time</th>
              <th scope="col">Route</th>
              <th scope="col">Fuel</th>
              <th scope="col" className="r">Litres</th>
              <th scope="col">Status</th>
              <th scope="col">Simulator</th>
              <th scope="col">Idempotency key</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((line) => {
              const st = line.station_id;
              const clip = clipInfo(line, routes);
              const sim = line.simulator;
              const tick = lineTick(line);
              const isOpen = open.has(line.id);
              const route = routes.find((r) => r.id === line.route_id);
              const reason = failureText(sim?.failure_reason ?? line.failure_code);
              return (
                <Fragment key={line.id}>
                  <tr className={`${s.row} ${state.fresh.has(line.id) ? `flash ${s.fresh}` : ''}`} data-status={line.status} data-open={isOpen || undefined}>
                    <td data-label="Line" className={s.cLine}>
                      <button type="button" className={s.expander} aria-expanded={isOpen} aria-controls={`ld-${line.id}`} onClick={() => flip(line.id)} aria-label={`Line ${line.id}, ${isOpen ? 'hide' : 'show'} details`}>
                        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" className={isOpen ? s.chevOpen : undefined}><path d="M3 1.5 7 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
                        <span className="mono">L{line.id}</span>
                      </button>
                    </td>
                    <td data-label="Proposal" className="mono">#{line.proposal_id}</td>
                    <td data-label="Time" className={s.cTime}>
                      <span className="mono">tick {tick}</span> <span className="mono muted">{tickClock(simTime, nowTick, tick, tm)}</span>
                    </td>
                    <td data-label="Route" className={s.cRoute}>{routeLabel(line, st)}</td>
                    <td data-label="Fuel"><FuelTag fuel={line.fuel_type} /></td>
                    <td data-label="Litres" className={`num ${s.cLiters}`}>
                      {clip.clipped && line.executed_quantity != null ? (
                        <>
                          <span><span className="faint">{int(line.proposed_quantity)}</span> → <strong>{int(line.executed_quantity)}</strong> L</span>
                          <span className={s.clip}>{clip.text}</span>
                        </>
                      ) : (
                        <span>{line.executed_quantity != null ? int(line.executed_quantity) : int(line.proposed_quantity)} L{line.executed_quantity == null && <span className={s.clip}>proposed, not yet executed</span>}</span>
                      )}
                    </td>
                    <td data-label="Status" className={s.cStatus}><StatusStepper status={line.status} /></td>
                    <td data-label="Simulator" className={s.cSim}>
                      {sim ? (
                        <>
                          <span className="mono">
                            {sim.actual_arrival_tick != null ? `arrived tick ${sim.actual_arrival_tick}` : sim.status === 'FAILED' || sim.status === 'CANCELLED' ? sim.status.toLowerCase() : sim.expected_arrival_tick != null ? `due tick ${sim.expected_arrival_tick}` : 'accepted'}
                          </span>
                          {reason && <span className={s.reason}>{reason}</span>}
                        </>
                      ) : (
                        <>
                          <span className="faint">no simulator record</span>
                          {reason && <span className={s.reason}>{reason}</span>}
                        </>
                      )}
                    </td>
                    <td data-label="Idempotency key" className={s.cKey}><CopyKey value={line.idempotency_key} label={`line ${line.id}`} /></td>
                  </tr>
                  {isOpen && (
                    <tr className={s.detailRow} id={`ld-${line.id}`}>
                      <td colSpan={COLS}>
                        <Detail line={line} station={st} world={world} routeMax={route?.max_shipment} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <Panel title="Shipment lines" meta={meta} actions={actions} flush>
      {state.loaded && total > 0 && (
        <div className={s.toolbar}>
          <div className={s.chips} role="group" aria-label="Filter by status">
            {ALL_STATUSES.map((st) => {
              const n = counts[st] ?? 0;
              const on = wanted.has(st);
              return (
                <button key={st} type="button" className={`${s.fchip} ${on ? s.fchipOn : ''}`} aria-pressed={on} disabled={n === 0 && !on} onClick={() => toggle(st)}>
                  <StatusGlyph status={st} />
                  {statusWord(st)}
                  <span className="mono">{n}</span>
                </button>
              );
            })}
          </div>
          <label className={s.select}>
            <span className="label">Station</span>
            <select className="field" value={station} onChange={(e) => setStation(e.target.value)}>
              <option value="">All stations</option>
              {stations.map((st) => <option key={st} value={st}>{pretty(st)}</option>)}
            </select>
          </label>
        </div>
      )}
      {state.error && state.loaded && (
        <p className={s.err} role="status">
          Could not refresh the ledger ({state.error}). Showing the last good read{state.okAt ? `, ${age((Date.now() - state.okAt) / 1000)} old` : ''}.
          <button type="button" className="btn sm" onClick={refresh}>Retry</button>
        </p>
      )}
      {body}
    </Panel>
  );
}
