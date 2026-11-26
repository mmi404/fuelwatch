'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { int, liters, tickClock, volume } from '@/lib/format';
import type { StockPoint } from '@/lib/world';
import { FUTURE_TICKS, Inbound, PAST_TICKS, Projection } from './model';
import css from './StockChart.module.css';

export const CHART_HEIGHT = 240;
const M = { top: 26, right: 18, bottom: 34, left: 44 };
const SPAN = PAST_TICKS + FUTURE_TICKS;
/** Fewer observed readings than this is not a line yet. */
export const MIN_OBSERVED = 3;

interface Props {
  /** "Mirpur Fuel Station petrol", for the accessible name. */
  name: string;
  nowTick: number;
  tickMinutes: number;
  simTime: string | null;
  capacity: number;
  inventory: number;
  observed: StockPoint[];
  projection: Projection | null;
  inbound: Inbound[];
}

/** Clean y ticks from 0 to capacity: a 1, 2, 2.5 or 5 step that gives about four intervals. */
function yTicks(capacity: number): number[] {
  const raw = capacity / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v < capacity - 1e-6; v += step) out.push(v);
  return out;
}

const axisNum = (v: number, compact: boolean) => (compact ? (v === 0 ? '0' : `${+(v / 1000).toFixed(1)}K`) : int(v));

function offsetLabel(ticks: number, tickMinutes: number): string {
  if (ticks === 0) return 'now';
  const m = Math.abs(ticks) * tickMinutes;
  const t = m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}` : `${m} min`;
  return `${ticks > 0 ? '+' : '−'}${t}`;
}

interface Row { kind: 'obs' | 'exp' | 'band' | 'ship'; value: string; label: string }

export default function StockChart({ name, nowTick, tickMinutes, simTime, capacity, inventory, observed, projection, inbound }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const [byKeys, setByKeys] = useState(false);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    setWidth(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const t0 = nowTick - PAST_TICKS;
  const t1 = nowTick + FUTURE_TICKS;
  const W = Math.max(width, 200);
  const plotW = W - M.left - M.right;
  const plotH = CHART_HEIGHT - M.top - M.bottom;
  const base = M.top + plotH;
  const cap = Math.max(capacity, 1);
  const x = (t: number) => M.left + ((t - t0) / SPAN) * plotW;
  const y = (v: number) => M.top + (1 - Math.min(Math.max(v, 0), cap) / cap) * plotH;
  const clock = (t: number) => tickClock(simTime, nowTick, t, tickMinutes);

  const seen = useMemo(() => {
    const m = new Map<number, number>();
    for (const p of observed) if (p.tick >= t0 && p.tick <= nowTick) m.set(p.tick, p.inventory);
    m.set(nowTick, inventory);
    return m;
  }, [observed, t0, nowTick, inventory]);
  const obsPoints = useMemo(() => [...seen.entries()].sort((a, b) => a[0] - b[0]), [seen]);
  const proj = projection?.points ?? [];
  const arrivalAt = useMemo(() => {
    const m = new Map<number, number>();
    for (const a of inbound) m.set(a.tick, (m.get(a.tick) ?? 0) + a.liters);
    return m;
  }, [inbound]);

  const line = (pts: [number, number][]) => pts.map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)} ${y(v).toFixed(1)}`).join('');
  const band = (hi: (p: (typeof proj)[number]) => number, lo: (p: (typeof proj)[number]) => number) =>
    proj.length < 2 ? '' : `${line(proj.map((p) => [p.tick, hi(p)]))}${proj.slice().reverse().map((p) => `L${x(p.tick).toFixed(1)} ${y(lo(p)).toFixed(1)}`).join('')}Z`;

  const compact = capacity >= 10000;
  const ticks = useMemo(() => {
    const t = yTicks(cap);
    const pxPer = plotH / cap;
    const keep = t.filter((v) => (cap - v) * pxPer > 14);
    return { grid: keep, all: [...keep, cap] };
  }, [cap, plotH]);
  const xStep = plotW < 300 ? 24 : 12;
  const xTicks: number[] = [];
  for (let o = -PAST_TICKS; o <= FUTURE_TICKS; o += xStep) xTicks.push(nowTick + o);

  const empty = projection?.empty ?? null;
  const emptyAt = empty ? empty.tick + empty.share : null;
  const emptyX = emptyAt === null ? 0 : x(emptyAt);
  const emptyNow = empty !== null && empty.tick <= nowTick;
  const emptyText = empty === null ? '' : emptyNow ? 'empty now' : `empty about ${clock(empty.tick)}`;
  const expectedPath: [number, number][] = [];
  proj.forEach((p) => {
    expectedPath.push([p.tick, p.mean]);
    // Draw the real touch-down, not a straight line to the next tick.
    if (empty && p.tick === empty.tick) expectedPath.push([emptyAt as number, 0]);
  });
  const collecting = obsPoints.length < MIN_OBSERVED;
  const collectingY = y(inventory) < M.top + plotH * 0.55 ? M.top + plotH * 0.72 : M.top + plotH * 0.34;
  const collectingX = (M.left + x(nowTick)) / 2;
  const arrivals = [...arrivalAt.entries()].filter(([t]) => t <= t1).sort((a, b) => a[0] - b[0]);
  const cover = projection && projection.coverTarget > 0 && projection.coverTarget < cap ? projection.coverTarget : null;

  // Latest-value label, left of the dot: under it when the observed line comes down from above, and off the cover line.
  const previous = obsPoints.length >= MIN_OBSERVED ? obsPoints[obsPoints.length - 2][1] : null;
  const under = y(inventory) < M.top + 14 || (previous !== null && previous > inventory && y(inventory) < base - 20);
  let stockLabelY = y(inventory) + (under ? 16 : -8);
  if (cover !== null && y(cover) > stockLabelY - 13 && y(cover) < stockLabelY + 5) stockLabelY = under ? y(cover) + 15 : y(cover) - 6;
  stockLabelY = Math.min(stockLabelY, base - 5);

  const rowsAt = (t: number): Row[] => {
    const out: Row[] = [];
    if (t < nowTick) {
      const v = seen.get(t);
      out.push(v === undefined ? { kind: 'obs', value: 'no reading', label: 'page was not open yet' } : { kind: 'obs', value: liters(v), label: 'observed' });
    } else if (t === nowTick) {
      out.push({ kind: 'obs', value: liters(inventory), label: 'observed now' });
    } else {
      const p = proj[t - nowTick];
      if (!p) out.push({ kind: 'exp', value: 'no forecast', label: 'beyond the forecast' });
      else {
        out.push({ kind: 'exp', value: liters(p.mean), label: 'expected' });
        out.push({ kind: 'band', value: `${liters(p.low)} to ${liters(p.high)}`, label: 'cautious range' });
      }
    }
    const a = arrivalAt.get(t);
    if (a) out.push({ kind: 'ship', value: `+${liters(a)}`, label: 'shipment arrives' });
    return out;
  };
  const cursorRows = cursor === null ? [] : rowsAt(cursor);
  const cursorValue = cursor === null ? null : cursor <= nowTick ? (seen.get(cursor) ?? null) : (proj[cursor - nowTick]?.mean ?? null);
  const readout = cursor === null ? '' : `${clock(cursor)}, ${offsetLabel(cursor - nowTick, tickMinutes)}. ${cursorRows.map((r) => `${r.label} ${r.value}`).join('. ')}`;

  const label = [
    `${name}: stock in litres from ${PAST_TICKS * tickMinutes / 60} hours back to ${FUTURE_TICKS * tickMinutes / 60} hours ahead.`,
    `Now ${liters(inventory)} of ${liters(capacity)}.`,
    projection ? (empty ? `At expected demand: ${emptyText}.` : `At expected demand it does not run empty in the next ${(FUTURE_TICKS * tickMinutes) / 60} hours.`) : 'No forecast loaded.',
    ...inbound.map((a) => `Shipment of ${liters(a.liters)} expected ${clock(a.tick)}.`),
    collecting ? `Observed history is still collecting: ${obsPoints.length} of ${MIN_OBSERVED} readings.` : '',
    'Use left and right arrow keys to read each tick.',
  ].join(' ');

  const clampTick = (t: number) => Math.min(t1, Math.max(t0, t));
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    if (px < M.left - 8 || px > W - M.right + 8) return;
    setByKeys(false);
    setCursor(clampTick(Math.round(t0 + ((px - M.left) / plotW) * SPAN)));
  };
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    const at = cursor ?? nowTick;
    const d: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, PageDown: -12, PageUp: 12 };
    let next: number | null = null;
    if (e.key in d) next = at + d[e.key];
    else if (e.key === 'Home') next = t0;
    else if (e.key === 'End') next = t1;
    if (next === null) return;
    e.preventDefault();
    setByKeys(true);
    setCursor(clampTick(next));
  };

  const tipLeft = cursor !== null && x(cursor) < W * 0.55;
  const surface = { stroke: 'var(--surface)', strokeWidth: 3, paintOrder: 'stroke' as const };

  return (
    <div ref={wrap} className={css.wrap}>
      <svg
        className={css.svg}
        width={W}
        height={CHART_HEIGHT}
        viewBox={`0 0 ${W} ${CHART_HEIGHT}`}
        role="application"
        aria-roledescription="stock chart"
        aria-label={label}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => !byKeys && setCursor(null)}
        onKeyDown={onKey}
        onFocus={() => setCursor((c) => c ?? nowTick)}
        onBlur={() => {
          setCursor(null);
          setByKeys(false);
        }}
      >
        {/* grid: solid hairlines, labels in litres */}
        <g shapeRendering="crispEdges">
          {ticks.all.map((v) => (
            <line key={v} x1={M.left} x2={W - M.right} y1={Math.round(y(v)) + 0.5} y2={Math.round(y(v)) + 0.5} stroke="var(--grid)" strokeWidth={1} />
          ))}
          <line x1={M.left} x2={W - M.right} y1={Math.round(base) + 0.5} y2={Math.round(base) + 0.5} stroke="var(--rule-strong)" strokeWidth={1} />
          {xTicks.map((t) => (
            <line key={t} x1={Math.round(x(t)) + 0.5} x2={Math.round(x(t)) + 0.5} y1={base} y2={base + 4} stroke="var(--rule-strong)" strokeWidth={1} />
          ))}
        </g>
        <g className={css.axis}>
          {ticks.all.map((v) => (
            <text key={v} x={M.left - 6} y={y(v) + 4} textAnchor="end">{axisNum(v, compact)}</text>
          ))}
          {xTicks.map((t) => (
            <text key={t} x={x(t)} y={base + 18} textAnchor="middle">{clock(t)}</text>
          ))}
          <text x={M.left - 6} y={12} textAnchor="end">L</text>
        </g>

        {/* the fan: outer band then darker core, from the real forecast */}
        {proj.length > 1 && (
          <g>
            <path d={band((p) => p.high, (p) => p.low)} fill="var(--band-outer)" />
            <path d={band((p) => p.coreHigh, (p) => p.coreLow)} fill="var(--band-core)" />
          </g>
        )}

        {/* cover target: 24 ticks of expected demand */}
        {cover !== null && (
          <g>
            <line x1={M.left} x2={W - M.right} y1={y(cover)} y2={y(cover)} stroke="var(--ink-2)" strokeWidth={1} shapeRendering="crispEdges" />
            <text className={css.ref} x={W - M.right - 4} y={y(cover) - 5} textAnchor="end" {...surface}>
              cover target {int(cover)} L
            </text>
          </g>
        )}

        {/* now divider */}
        <line x1={x(nowTick)} x2={x(nowTick)} y1={M.top - 6} y2={base} stroke="var(--ink-3)" strokeWidth={1} shapeRendering="crispEdges" />
        <text className={css.strong} x={x(nowTick)} y={12} textAnchor="middle">now</text>

        {/* observed (solid) and projected at expected demand (lighter) */}
        {!collecting && <path d={line(obsPoints)} fill="none" stroke="var(--ink)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
        {collecting && (
          <g>
            {obsPoints.slice(0, -1).map(([t, v]) => (
              <circle key={t} cx={x(t)} cy={y(v)} r={4} fill="var(--ink)" stroke="var(--surface)" strokeWidth={2} />
            ))}
            <text className={css.ref} x={collectingX} y={collectingY} textAnchor="middle" {...surface}>collecting</text>
            <text className={css.ref} x={collectingX} y={collectingY + 14} textAnchor="middle" {...surface}>{obsPoints.length} of {MIN_OBSERVED} readings</text>
          </g>
        )}
        {proj.length > 1 && <path d={line(expectedPath)} fill="none" stroke="var(--ink)" strokeOpacity={0.5} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}

        {/* the empty marker */}
        {empty !== null && (
          <g>
            <line x1={emptyX} x2={emptyX} y1={M.top} y2={base} stroke="var(--crit)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
            <circle cx={emptyX} cy={base} r={4} fill="var(--crit)" stroke="var(--surface)" strokeWidth={2} />
            <text className={css.strong} x={emptyX + (emptyX > W - 130 ? -6 : 6)} y={M.top + 12} textAnchor={emptyX > W - 130 ? 'end' : 'start'} {...surface}>{emptyText}</text>
          </g>
        )}

        {/* shipments on the road: a triangle on the x axis with its litres */}
        {arrivals.map(([t, litres], i) => {
          const ax = x(t);
          const right = ax > W - 60;
          // The label sits above the stock the delivery leads to, clear of the cover line and of its neighbours.
          const after = proj[t - nowTick + 1]?.mean ?? 0;
          let ly = Math.min(base - 14, y(after) - 9);
          if (cover !== null && ly > y(cover) - 18 && ly < y(cover) + 12) ly = y(cover) - 20;
          if (i > 0 && ax - x(arrivals[i - 1][0]) < 58 && i % 2 === 1) ly -= 14;
          ly = Math.max(ly, M.top + 28);
          return (
            <g key={t}>
              <path d={`M${ax} ${base - 7}L${ax + 5} ${base + 1}L${ax - 5} ${base + 1}Z`} fill="var(--ink)" stroke="var(--surface)" strokeWidth={2} paintOrder="stroke" />
              <text className={css.ref} x={ax + (right ? 6 : -4)} y={ly} textAnchor={right ? 'end' : 'start'} {...surface}>+{volume(litres)}</text>
            </g>
          );
        })}

        {/* latest stock value */}
        <circle cx={x(nowTick)} cy={y(inventory)} r={4} fill="var(--ink)" stroke="var(--surface)" strokeWidth={2} />
        <text className={css.strong} x={x(nowTick) - 9} y={stockLabelY} textAnchor="end" {...surface}>{liters(inventory)}</text>

        {/* crosshair snapped to the nearest tick */}
        {cursor !== null && (
          <g pointerEvents="none">
            <line x1={x(cursor)} x2={x(cursor)} y1={M.top - 6} y2={base} stroke="var(--ink)" strokeWidth={1} shapeRendering="crispEdges" />
            {cursorValue !== null && <circle cx={x(cursor)} cy={y(cursorValue)} r={4} fill="var(--ink)" stroke="var(--surface)" strokeWidth={2} />}
          </g>
        )}
      </svg>

      {cursor !== null && (
        <div className={css.tip} aria-hidden="true" style={tipLeft ? { left: x(cursor) + 12 } : { right: W - x(cursor) + 12 }}>
          <div className={css.tipHead}>
            <span className={css.tipTime}>{clock(cursor)}</span>
            <span>{offsetLabel(cursor - nowTick, tickMinutes)}</span>
          </div>
          {cursorRows.map((r) => (
            <div key={r.kind} className={css.tipRow}>
              <span className={`${css.key} ${css[r.kind]}`} />
              <span className={css.tipValue}>{r.value}</span>
              <span>{r.label}</span>
            </div>
          ))}
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">{byKeys ? readout : ''}</span>

      {/* the wrapper is what hides it: a table ignores a 1px width and would stretch the sheet */}
      <div className="sr-only">
      <table>
        <caption>{`${name}: stock in litres per tick`}</caption>
        <thead>
          <tr><th>Time</th><th>Tick</th><th>Observed</th><th>Projected, expected demand</th><th>Cautious low</th><th>Cautious high</th><th>Shipment arriving</th></tr>
        </thead>
        <tbody>
          {Array.from({ length: SPAN + 1 }, (_, i) => t0 + i).map((t) => {
            const p = t > nowTick ? proj[t - nowTick] : t === nowTick ? proj[0] : undefined;
            const o = seen.get(t);
            return (
              <tr key={t}>
                <td>{clock(t)}</td>
                <td>{t}</td>
                <td>{o === undefined ? 'none' : int(o)}</td>
                <td>{p ? int(p.mean) : 'none'}</td>
                <td>{p && t > nowTick ? int(p.low) : 'none'}</td>
                <td>{p && t > nowTick ? int(p.high) : 'none'}</td>
                <td>{arrivalAt.get(t) ? int(arrivalAt.get(t) as number) : 'none'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}
