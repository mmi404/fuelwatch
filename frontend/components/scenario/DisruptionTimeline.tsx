'use client';

import { useEffect, useId, useState } from 'react';
import { Panel } from '@/components/ui/Panel';
import { SimEvent, SupplyArrival } from '@/lib/api';
import { fuelName, liters, plural } from '@/lib/format';
import { useLive } from '@/lib/world';
import { describeEvent, makeNames } from './util';
import s from './Scenario.module.css';

const PAD_L = 12;
const PAD_R = 14;
const LABEL_IN = 6; // lane labels start clear of a now line at tick 0
const TOP = 24; // room for the "now" label
const EVENT_ROW = 40; // label line, then the bar
const BAR_H = 14;
const LANE_HEAD = 22;
const SUPPLY_ROW = 32;
const AXIS_H = 36;
const CHAR_W = 6.6; // 12 px IBM Plex Sans, a little above average so labels do not run off

const STATUS_WORD: Record<string, string> = { SCHEDULED: 'Scheduled', ACTIVE: 'Active', RESOLVED: 'Ended' };
const statusWord = (st: string) => STATUS_WORD[st] ?? st.charAt(0) + st.slice(1).toLowerCase();
type Kind = 'scheduled' | 'active' | 'ended';
const kindOf = (st: string): Kind => (st === 'SCHEDULED' ? 'scheduled' : st === 'ACTIVE' ? 'active' : 'ended');
const BAR: Record<Kind, string> = { active: s.barActive, scheduled: s.barScheduled, ended: s.barEnded };
const markOf = (st: string) => (st === 'ARRIVED' ? s.markArrived : st === 'DELAYED' ? s.markDelayed : s.markScheduled);
/** Where an arrival sits: when it happened if it has, else when it is due. */
const arrivalTick = (a: SupplyArrival) => a.actual_tick ?? a.planned_tick;

const fit = (text: string, maxChars: number) => (text.length <= maxChars ? text : `${text.slice(0, Math.max(4, maxChars - 1)).trimEnd()}…`);

function niceStep(span: number, slots: number): number {
  for (const step of [4, 8, 12, 24, 48, 96, 192]) if (span / step <= slots) return step;
  return 384;
}

/** Simulated day and time of a tick. Tick 0 is 00:00 on day 1. */
function dayTime(tick: number, tickMinutes: number): string {
  const total = tick * tickMinutes;
  const p = (n: number) => String(n).padStart(2, '0');
  return `d${Math.floor(total / 1440) + 1} ${p(Math.floor((total % 1440) / 60))}:${p(total % 60)}`;
}

const diamond = (cx: number, cy: number, r: number) => `M${cx} ${cy - r}L${cx + r} ${cy}L${cx} ${cy + r}L${cx - r} ${cy}Z`;

/** The pattern used for scheduled bars. Its id is unique per drawing, so the legend and the chart each define their own. */
function Hatch({ id }: { id: string }) {
  return (
    <pattern id={id} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="6" className={s.hatchLine} />
    </pattern>
  );
}

function Swatch({ kind, hatch }: { kind: Kind; hatch: string }) {
  return (
    <svg width="26" height="12" aria-hidden="true" focusable="false" className={s.swatch}>
      {kind === 'scheduled' && <defs><Hatch id={hatch} /></defs>}
      <rect x="1" y="1" width="24" height="10" className={BAR[kind]} style={kind === 'scheduled' ? { fill: `url(#${hatch})` } : undefined} />
    </svg>
  );
}

function Mark({ status }: { status: string }) {
  return (
    <svg width="12" height="12" aria-hidden="true" focusable="false" className={s.swatch}>
      <path d={diamond(6, 6, 4.5)} className={markOf(status)} />
    </svg>
  );
}

/** Hand-drawn Gantt: disruption bars over ticks, then planned supply arrivals per depot. */
export default function DisruptionTimeline() {
  const { snapshot, error } = useLive();
  const world = snapshot?.world ?? null;
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const pid = useId().replace(/[^a-zA-Z0-9]/g, '');

  // The chart box appears only once there is data, so the observer follows the element, not the first render.
  useEffect(() => {
    if (!box) return;
    const measure = () => setWidth(Math.max(260, Math.floor(box.clientWidth)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    return () => ro.disconnect();
  }, [box]);

  if (!world) {
    return (
      <Panel title="Disruption timeline" meta="waiting for data">
        <p className={s.empty}>{error ? 'No world state to draw yet. The console keeps trying.' : 'Waiting for the first world state.'}</p>
      </Panel>
    );
  }

  const names = makeNames(world);
  const events: SimEvent[] = [...world.events].sort((a, b) => a.start_tick - b.start_tick || a.id - b.id);
  const arrivals = [...world.supply_arrivals].sort((a, b) => arrivalTick(a) - arrivalTick(b) || a.id.localeCompare(b.id));
  const depotIds = [...new Set(arrivals.map((a) => a.depot_id))].sort();
  const nowTick = world.instance.tick;
  const tickMin = world.instance.tick_minutes;

  const end = Math.max(nowTick + 24, ...events.map((e) => e.end_tick), ...arrivals.map(arrivalTick)) + 2;
  const inner = Math.max(1, width - PAD_L - PAD_R);
  const x = (t: number) => PAD_L + (Math.min(Math.max(t, 0), end) / end) * inner;
  const maxChars = Math.floor(inner / CHAR_W);

  const eventsH = events.length === 0 ? 30 : events.length * EVENT_ROW;
  const supplyTop = TOP + LANE_HEAD + eventsH + 10;
  const axisY = supplyTop + LANE_HEAD + depotIds.length * SUPPLY_ROW + 4;
  const height = axisY + AXIS_H;
  const step = niceStep(end, Math.max(2, Math.floor(inner / 72)));
  const ticks: number[] = [];
  for (let t = 0; t <= end; t += step) ticks.push(t);

  const count = (st: string) => arrivals.filter((a) => a.status === st).length;
  const length = (e: SimEvent) => {
    const hours = ((e.end_tick - e.start_tick) * tickMin) / 60;
    return hours < 1 ? `${Math.round(hours * 60)} min` : `${Number(hours.toFixed(1))} h`;
  };
  const eventLine = (e: SimEvent) => `${describeEvent(e, names)}. ${statusWord(e.status)}, ticks ${e.start_tick} to ${e.end_tick} (${length(e)}).`;
  const arrivalLine = (a: SupplyArrival) =>
    `${names.depot(a.depot_id)} depot, ${fuelName(a.fuel_type)}, ${liters(a.quantity)}: ${a.status.toLowerCase()}, ${a.actual_tick != null ? `arrived at tick ${a.actual_tick}` : `due at tick ${a.planned_tick}`}.`;

  const active = events.filter((e) => e.status === 'ACTIVE').length;
  const summary = `Disruption timeline, ticks 0 to ${end}. Now is tick ${nowTick}. ${
    events.length === 0 ? 'No disruptions.' : `${plural(events.length, 'disruption')}, ${active} active.`
  } ${plural(arrivals.length, 'supply arrival')}. The lists below carry the same information.`;

  const hovered = (() => {
    const e = events.find((ev) => `e${ev.id}` === hover);
    if (e) return eventLine(e);
    const a = arrivals.find((ar) => `a${ar.id}` === hover);
    return a ? arrivalLine(a) : null;
  })();

  const nowRight = x(nowTick) > width - 110;

  return (
    <Panel title="Disruption timeline" meta={`now at tick ${nowTick}`}>
      <div className="dimmable">
        <div className={s.legend}>
          <span><Swatch kind="active" hatch={`${pid}-l1`} />Active</span>
          <span><Swatch kind="scheduled" hatch={`${pid}-l2`} />Scheduled</span>
          <span><Swatch kind="ended" hatch={`${pid}-l3`} />Ended</span>
          <span className={s.legendSep} aria-hidden="true" />
          <span><Mark status="ARRIVED" />Supply arrived</span>
          <span><Mark status="SCHEDULED" />Supply due</span>
          <span><Mark status="DELAYED" />Supply delayed</span>
        </div>

        <div ref={setBox} className={s.chart}>
          {width > 0 && (
            <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary} className={s.svg}>
              <defs>
                <Hatch id={`${pid}-bar`} />
              </defs>

              {ticks.map((t) => (
                <line key={`g${t}`} x1={x(t)} x2={x(t)} y1={TOP} y2={axisY} className={s.gridLine} shapeRendering="crispEdges" />
              ))}
              <line x1={PAD_L} x2={width - PAD_R} y1={axisY} y2={axisY} className={s.axis} shapeRendering="crispEdges" />
              {ticks.map((t) => {
                const anchor = t === 0 ? 'start' : x(t) > width - PAD_R - 30 ? 'end' : 'middle';
                return (
                  <g key={`a${t}`}>
                    <text x={x(t)} y={axisY + 15} textAnchor={anchor} className={s.tick}>{t}</text>
                    <text x={x(t)} y={axisY + 29} textAnchor={anchor} className={s.tick2}>{dayTime(t, tickMin)}</text>
                  </g>
                );
              })}

              {/* Under the labels and marks, so the line never strikes through either. */}
              <line x1={x(nowTick)} x2={x(nowTick)} y1={TOP - 4} y2={axisY} className={s.now} />

              <text x={PAD_L + LABEL_IN} y={TOP + 12} className={s.lane}>DISRUPTIONS</text>
              {events.length === 0 && <text x={PAD_L + LABEL_IN} y={TOP + LANE_HEAD + 14} className={s.emptySvg}>None in the world right now.</text>}
              {events.map((e, i) => {
                const y0 = TOP + LANE_HEAD + i * EVENT_ROW;
                const x1 = x(e.start_tick);
                const x2 = Math.max(x(e.end_tick), x1 + 4);
                const kind = kindOf(e.status);
                const text = fit(`${describeEvent(e, names)} · ${statusWord(e.status)}`, maxChars);
                const rightAlign = x1 + text.length * CHAR_W > width - PAD_R;
                return (
                  <g key={e.id} onMouseEnter={() => setHover(`e${e.id}`)} onMouseLeave={() => setHover(null)}>
                    <rect x={PAD_L} y={y0} width={inner} height={EVENT_ROW} className={s.hit} />
                    <text x={rightAlign ? width - PAD_R : x1} y={y0 + 12} textAnchor={rightAlign ? 'end' : 'start'} className={s.evLabel}>{text}</text>
                    <rect x={x1} y={y0 + 18} width={x2 - x1} height={BAR_H} className={BAR[kind]} style={kind === 'scheduled' ? { fill: `url(#${pid}-bar)` } : undefined} />
                  </g>
                );
              })}

              <text x={PAD_L + LABEL_IN} y={supplyTop + 12} className={s.lane}>SUPPLY ARRIVALS</text>
              {depotIds.map((dep, i) => {
                const y0 = supplyTop + LANE_HEAD + i * SUPPLY_ROW;
                const seen = new Map<number, number>();
                return (
                  <g key={dep}>
                    <text x={PAD_L + LABEL_IN} y={y0 + 10} className={s.depotLabel}>{names.depot(dep)} depot</text>
                    <line x1={PAD_L} x2={width - PAD_R} y1={y0 + 22} y2={y0 + 22} className={s.laneLine} shapeRendering="crispEdges" />
                    {arrivals.filter((a) => a.depot_id === dep).map((a) => {
                      const at = arrivalTick(a);
                      const same = seen.get(at) ?? 0;
                      seen.set(at, same + 1);
                      const cx = x(at) + same * 7;
                      return (
                        <g key={a.id} onMouseEnter={() => setHover(`a${a.id}`)} onMouseLeave={() => setHover(null)}>
                          <circle cx={cx} cy={y0 + 22} r={9} className={s.hit} />
                          <path d={diamond(cx, y0 + 22, 5)} className={markOf(a.status)} />
                        </g>
                      );
                    })}
                  </g>
                );
              })}

              <text x={x(nowTick) + (nowRight ? -6 : 6)} y={TOP - 9} textAnchor={nowRight ? 'end' : 'start'} className={s.nowLabel}>
                Now · tick {nowTick}
              </text>
            </svg>
          )}
        </div>

        <p className={s.readoutLine}>{hovered ?? 'Point at a bar or a mark for its details. The numbers along the bottom are ticks, then the simulated day and time.'}</p>

        <h3 className={s.listHead}>Disruptions</h3>
        {events.length === 0 ? (
          <p className={s.hint}>
            None. This page cannot add them: the backend has no way to do that. They show up here once they exist in the simulator.
          </p>
        ) : (
          <ul className={s.eventList}>
            {events.map((e) => (
              <li key={e.id}>
                <Swatch kind={kindOf(e.status)} hatch={`${pid}-i${e.id}`} />
                <span>{eventLine(e)}</span>
              </li>
            ))}
          </ul>
        )}

        <details className={s.supply}>
          <summary>
            {plural(arrivals.length, 'supply arrival')}: {count('ARRIVED')} arrived, {count('SCHEDULED')} due, {count('DELAYED')} delayed
          </summary>
          <div className={s.supplyScroll} tabIndex={0} role="region" aria-label="Supply arrivals">
            <table className={`tbl ${s.supplyTable}`}>
              <thead>
                <tr>
                  <th scope="col">Depot</th>
                  <th scope="col">Fuel</th>
                  <th scope="col" className="r">Litres</th>
                  <th scope="col" className="r">Tick</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {arrivals.map((a) => (
                  <tr key={a.id}>
                    <td>{names.depot(a.depot_id)}</td>
                    <td>{fuelName(a.fuel_type)}</td>
                    <td className="num">{liters(a.quantity)}</td>
                    <td className="num">{arrivalTick(a)}</td>
                    <td><span className={s.state}><Mark status={a.status} />{a.status === 'SCHEDULED' ? 'Due' : statusWord(a.status)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </Panel>
  );
}
