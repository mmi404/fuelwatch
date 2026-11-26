'use client';

import { useMemo } from 'react';
import type { Level, Risk, Route, World } from '@/lib/api';
import { LevelGlyph, levelLabel } from '@/components/ui/Level';
import { fuelName, int, liters, tickClock, tickHours } from '@/lib/format';
import { FUELS, fuelColor, fuelLetter, levelColor, levelRank, pairKey, splitKey } from '@/lib/risk';
import { bezier, edgeLevel, fmtH, outlook, placeLabel, shipmentsInFlight, shortName, stationLine, textW } from './layout';
import type { Edge, Layout, Shipment, Slot, StationView } from './layout';
import styles from './NetworkMap.module.css';

export interface Hover { kind: 'depot' | 'station' | 'route'; id: string }
export interface Hot { routes: Set<string>; depots: Set<string>; stations: Set<string> }

/** Everything a hover or focus lights up: the thing itself plus the other ends of its routes. */
export function hotSet(h: Hover | null, routes: Route[]): Hot | null {
  if (!h) return null;
  const hot: Hot = { routes: new Set(), depots: new Set(), stations: new Set() };
  for (const r of routes) {
    const match = h.kind === 'route' ? r.id === h.id : h.kind === 'depot' ? r.source_depot_id === h.id : r.destination_station_id === h.id;
    if (!match) continue;
    hot.routes.add(r.id);
    hot.depots.add(r.source_depot_id);
    hot.stations.add(r.destination_station_id);
  }
  if (h.kind === 'depot') hot.depots.add(h.id);
  if (h.kind === 'station') hot.stations.add(h.id);
  return hot;
}

interface Props {
  world: World;
  risks: Risk[];
  layout: Layout;
  views: Map<string, StationView>;
  hot: Hot | null;
  setHover: (h: Hover | null) => void;
  selected: string | null;
  select: (key: string | null) => void;
  label: string;
  patternId: string;
}

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(' ');

export default function NetworkSvg({ world, risks, layout, views, hot, setHover, selected, select, label, patternId }: Props) {
  const { instance } = world;
  const tick = instance.tick;
  const tm = instance.tick_minutes;
  const narrow = layout.narrow;
  const depotOpen = new Map(world.depots.map((d) => [d.id, d.status === 'OPEN']));
  const stationOpen = new Map(world.stations.map((s) => [s.id, s.status === 'OPEN']));
  const ships = useMemo(() => shipmentsInFlight(world.allocations, world.routes, tick), [world.allocations, world.routes, tick]);

  // label slots: closed-route stamps first (they are the news), then shipment markers, transit times and ETAs
  const { labels, stamps, shipLabels } = useMemo(() => {
    const taken: Slot[] = [];
    const stamps = new Map<string, { x: number; y: number }>();
    const labels = new Map<string, { x: number; y: number; text: string }>();
    for (const e of layout.edges) {
      if (e.route.status === 'AVAILABLE') continue;
      const p = placeLabel(e, 68, 18, taken, [0.5, 0.36, 0.64]);
      if (p) stamps.set(e.route.id, p);
    }
    const edgeOf = new Map(layout.edges.map((e) => [e.route.id, e]));
    const shipLabels = new Map<number, { x: number; y: number; w: number; text: string; level: Level | null; px: number; py: number; r: number }>();
    const maxShip = Math.max(1, ...layout.edges.map((e) => e.route.max_shipment));
    // a shipment's position is a fact, so its marker takes its place first and the labels move around it
    const markers = new Map<number, { x: number; y: number; r: number }>();
    for (const s of ships) {
      const e = edgeOf.get(s.route.id);
      if (!e) continue;
      // kept clear of both node columns, so a shipment that has not left yet is still readable
      const t = Math.min(narrow ? 0.76 : 0.88, Math.max(narrow ? 0.24 : 0.1, s.t));
      const p = bezier(e, t);
      const r = 8 + 5 * Math.sqrt(Math.min(1, s.quantity / maxShip));
      markers.set(s.id, { x: p.x, y: p.y, r });
      taken.push({ cx: p.x, cy: p.y, w: r * 2, h: r * 2 });
    }
    for (const e of layout.edges) {
      const text = fmtH(tickHours(e.route.transit_ticks, tm));
      const p = placeLabel(e, textW(text, 11, 0.6) + 4, 14, taken);
      if (p) labels.set(e.route.id, { x: p.x, y: p.y, text });
    }
    for (const s of ships) {
      const m = markers.get(s.id);
      if (!m) continue;
      const p = { x: m.x, y: m.y };
      const r = m.r;
      const text = etaText(s, world.instance.sim_time, tick, tm, narrow);
      const risk = risks.find((x) => x.station_id === s.route.destination_station_id && x.fuel_type === s.fuel);
      const level = risk && risk.level !== 'UNKNOWN' && levelRank(risk.level) >= levelRank('WATCH') ? risk.level : null;
      const w = textW(text, 11, 0.6) + 8 + (level ? 16 : 0);
      const x = Math.min(layout.zone.x1 - w / 2 - 1, Math.max(layout.zone.x0 + w / 2 + 1, p.x));
      let y = p.y - (r + 14);
      for (const dy of [-(r + 14), r + 14, -(r + 30), r + 30, -(r + 46), r + 46]) {
        const slot: Slot = { cx: x, cy: p.y + dy, w, h: 15 };
        if (slot.cy < 9 || slot.cy > layout.rulerY - 10) continue;
        const clash = taken.some((o) => Math.abs(o.cx - slot.cx) < (o.w + slot.w) / 2 && Math.abs(o.cy - slot.cy) < (o.h + slot.h) / 2);
        if (!clash) {
          taken.push(slot);
          y = slot.cy;
          break;
        }
      }
      shipLabels.set(s.id, { x, y, w, text, level, px: p.x, py: p.y, r });
    }
    return { labels, stamps, shipLabels };
  }, [layout, ships, risks, tm, tick, narrow, world.instance.sim_time]);

  const edgeOrder = useMemo(() => {
    const rank = (e: Edge) => {
      const lv = edgeLevel(e.route, ships, risks);
      return (e.route.status !== 'AVAILABLE' ? 1 : 0) + (lv ? 2 : 0);
    };
    return [...layout.edges].sort((a, b) => rank(a) - rank(b) || a.route.id.localeCompare(b.route.id));
  }, [layout.edges, ships, risks]);

  const edgeById = new Map(layout.edges.map((e) => [e.route.id, e]));
  const pad = narrow ? 8 : 10;
  const rulerY = layout.rulerY;
  const names = new Map<string, string>([...world.depots, ...world.stations].map((n) => [n.id, shortName(n.name)]));

  return (
    <svg className={styles.svg} viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" role="img" aria-label={label} focusable="false">
      <defs>
        <pattern id={patternId} width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="7" className={styles.hatchLine} strokeWidth="2" />
        </pattern>
      </defs>

      {/* hairline ruler: where along a route a shipment is, as a share of its transit time */}
      <g className={styles.ruler} aria-hidden="true">
        <line x1={layout.zone.x0} x2={layout.zone.x1} y1={rulerY} y2={rulerY} />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const x = layout.zone.x0 + (layout.zone.x1 - layout.zone.x0) * f;
          return <line key={f} x1={x} x2={x} y1={rulerY} y2={rulerY + (f === 0 || f === 1 ? 7 : 4)} />;
        })}
        <text x={layout.zone.x0 + 2} y={rulerY + 19} textAnchor="start">depart</text>
        <text x={layout.zone.x1 - 2} y={rulerY + 19} textAnchor="end">arrive</text>
      </g>

      {/* edges */}
      {edgeOrder.map((e) => {
        const r = e.route;
        const closed = r.status !== 'AVAILABLE';
        const down = !closed && (!depotOpen.get(r.source_depot_id) || !stationOpen.get(r.destination_station_id));
        const lv = edgeLevel(r, ships, risks);
        const dim = hot != null && !hot.routes.has(r.id);
        const style: React.CSSProperties = { strokeWidth: e.width };
        if (closed) style.stroke = 'var(--crit)';
        else if (down) style.stroke = 'var(--outage)';
        else if (lv) style.stroke = levelColor(lv);
        const t = fmtH(tickHours(r.transit_ticks, tm));
        return (
          <g key={r.id} className={cx(styles.edgeG, dim && styles.dim)}>
            <path d={e.path} className={styles.edgeHit} onMouseEnter={() => setHover({ kind: 'route', id: r.id })} onMouseLeave={() => setHover(null)} />
            <path d={e.path} className={cx(styles.edge, closed && styles.edgeClosed, down && styles.edgeDown, hot?.routes.has(r.id) && !closed && !down && !lv && styles.edgeHot)} style={style} />
            <title>{`${names.get(r.source_depot_id)} to ${names.get(r.destination_station_id)}: ${t}, max ${liters(r.max_shipment)}${closed ? `, ${r.status.toLowerCase()}` : ''}`}</title>
          </g>
        );
      })}

      {/* closed stamps and transit times */}
      {layout.edges.map((e) => {
        const dim = hot != null && !hot.routes.has(e.route.id);
        const stamp = stamps.get(e.route.id);
        const lab = labels.get(e.route.id);
        return (
          <g key={e.route.id} className={cx(dim && styles.dim)} pointerEvents="none">
            {stamp && (
              <g transform={`translate(${stamp.x - 34},${stamp.y})`} className={styles.stamp}>
                <rect x="-8" y="-8" width="16" height="16" rx="2" />
                <path d="M-3.5 -3.5 3.5 3.5M3.5 -3.5 -3.5 3.5" />
                <text x="13" y="4">CLOSED</text>
              </g>
            )}
            {lab && (
              <>
                <rect x={lab.x - (lab.text.length * 6.6 + 6) / 2} y={lab.y - 7} width={lab.text.length * 6.6 + 6} height="14" rx="2" className={styles.labelBg} />
                <text x={lab.x} y={lab.y + 4} textAnchor="middle" className={styles.edgeLabel}>
                  {lab.text}
                </text>
              </>
            )}
          </g>
        );
      })}

      {/* depots */}
      {layout.depots.map((b) => {
        const d = b.depot;
        const open = d.status === 'OPEN';
        const dim = hot != null && !hot.depots.has(b.id);
        const barW = b.w - 28 - 10;
        const name = textW(d.name, 12, 0.6) <= b.w - 16 ? d.name : shortName(d.name);
        return (
          <g
            key={b.id}
            className={cx(styles.node, dim && styles.dim, hot?.depots.has(b.id) && styles.nodeHot)}
            transform={`translate(${b.x},${b.y})`}
            tabIndex={0}
            role="button"
            aria-label={`${d.name}, ${open ? 'open' : `outage, status ${d.status.toLowerCase()}`}. ${FUELS.map((f) => `${fuelName(f)} ${int(d.inventory[f] ?? 0)} of ${int(d.capacity[f] ?? 0)} litres`).join(', ')}.`}
            onMouseEnter={() => setHover({ kind: 'depot', id: b.id })}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover({ kind: 'depot', id: b.id })}
            onBlur={() => setHover(null)}
          >
            <rect className={styles.nodeBox} width={b.w} height={b.h} rx="2" />
            {!open && <rect width={b.w} height={b.h} rx="2" fill={`url(#${patternId})`} className={styles.hatch} />}
            {!open && <rect className={styles.nodeBox} width={b.w} height={b.h} rx="2" fill="none" />}
            <text x={pad} y="18" className={styles.nodeName}>{name}</text>
            {open ? (
              <text x={pad} y="33" className={styles.sub}>{narrow ? `${int(d.dispatch_capacity_per_tick)} L/tick` : `dispatch ${int(d.dispatch_capacity_per_tick)} L per tick`}</text>
            ) : (
              <g transform="translate(0,0)">
                <rect x={pad - 3} y="21" width={narrow ? 74 : 84} height="16" rx="2" className={styles.stampBg} />
                <g transform={`translate(${pad},23)`}><LevelGlyph level="OUTAGE" size={12} /></g>
                <text x={pad + 17} y="33" className={styles.stampText}>OUTAGE</text>
              </g>
            )}
            {FUELS.map((f, i) => {
              const cap = d.capacity[f] ?? 0;
              const inv = d.inventory[f] ?? 0;
              const frac = cap > 0 ? Math.min(1, Math.max(0, inv / cap)) : 0;
              const y = 44 + i * 16;
              return (
                <g key={f}>
                  <title>{`${fuelName(f)}: ${liters(inv)} of ${liters(cap)}`}</title>
                  <FuelBox x={pad - 2} y={y} fuel={f} />
                  <rect x="28" y={y + 4} width={barW} height="6" className={styles.track} />
                  <rect x="28" y={y + 4} width={Math.max(frac > 0 ? 1 : 0, barW * frac)} height="6" className={styles.fill} />
                  <text x={b.w - 8} y={y + 12} textAnchor="end" className={styles.litres}>{liters(inv)}</text>
                </g>
              );
            })}
          </g>
        );
      })}

      {/* stations */}
      {layout.stations.map((b) => {
        const s = b.station;
        const v = views.get(s.id);
        const worst = v?.worst ?? null;
        const level: Level = worst?.level ?? 'UNKNOWN';
        const dim = hot != null && !hot.stations.has(b.id);
        const isSel = selected != null && splitKey(selected).stationId === s.id;
        const name = textW(s.name, 12, 0.6) <= b.w - 16 ? s.name : shortName(s.name);
        const down = s.status !== 'OPEN';
        const row2 = stationLine(v, down, narrow, levelLabel(level));
        const select1 = () => worst && select(pairKey(worst.station_id, worst.fuel_type));
        const gap = (b.w - pad * 2) / 3;
        return (
          <g
            key={b.id}
            className={cx(styles.node, styles.station, dim && styles.dim, hot?.stations.has(b.id) && styles.nodeHot, isSel && styles.nodeSel)}
            transform={`translate(${b.x},${b.y})`}
            tabIndex={0}
            role="button"
            aria-label={`${s.name}${down ? `, outage, status ${s.status.toLowerCase()}` : ''}${v?.affected ? ', affected by a disruption' : ''}. ${worst ? `Worst pair ${fuelName(worst.fuel_type)}, ${levelLabel(level)}, ${outlook(worst)}. Press Enter to open it.` : 'No forecast.'}`}
            aria-pressed={isSel}
            onClick={select1}
            onKeyDown={(ev) => {
              if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                select1();
              }
            }}
            onMouseEnter={() => setHover({ kind: 'station', id: b.id })}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover({ kind: 'station', id: b.id })}
            onBlur={() => setHover(null)}
          >
            <rect className={styles.nodeBox} width={b.w} height={b.h} rx="2" />
            {down && <rect width={b.w} height={b.h} rx="2" fill={`url(#${patternId})`} className={styles.hatch} />}
            {v?.affected && <rect className={styles.ring} x="-3" y="-3" width={b.w + 6} height={b.h + 6} rx="2" />}
            <text x={pad} y="18" className={styles.nodeName}>{name}</text>
            {down && <rect x={pad - 3} y="23" width={Math.min(b.w - pad, textW(row2, 11, 0.6) + 28)} height="18" rx="2" className={styles.stampBg} />}
            <g transform={`translate(${pad - 1},25)`}><LevelGlyph level={down ? 'OUTAGE' : level} size={14} /></g>
            <text x={pad + 18} y="36" className={styles.status}>{row2}</text>
            {FUELS.map((f, i) => {
              const r = v?.byFuel.get(f);
              const l: Level = r?.level ?? 'UNKNOWN';
              const x = pad + i * gap;
              return (
                <g
                  key={f}
                  transform={`translate(${x},46)`}
                  className={styles.tick}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    if (r) select(pairKey(r.station_id, r.fuel_type));
                  }}
                >
                  <title>{`${fuelName(f)}: ${levelLabel(l)}${r ? `, ${outlook(r)}` : ''}`}</title>
                  <rect x="-3" y="-4" width="38" height="24" className={styles.tickHit} />
                  <FuelBox x={0} y={0} fuel={f} />
                  <g transform="translate(19,1)"><LevelGlyph level={l} size={13} /></g>
                </g>
              );
            })}
          </g>
        );
      })}

      {/* shipments in flight */}
      {ships.map((s) => {
        const e = edgeById.get(s.route.id);
        const lab = shipLabels.get(s.id);
        if (!e || !lab) return null;
        const dim = hot != null && !hot.routes.has(s.route.id);
        const above = lab.y < lab.py;
        const ghostR = narrow ? 6 : lab.r;
        return (
          <g key={s.id} className={cx(styles.ship, dim && styles.dim)}>
            <circle cx={e.x1 - ghostR - 2} cy={e.y1} r={ghostR} className={styles.ghost} style={{ stroke: fuelColor(s.fuel) }} />
            <line x1={lab.px} y1={lab.py + (above ? -lab.r : lab.r)} x2={lab.x} y2={lab.y + (above ? 8 : -8)} className={styles.leader} />
            <circle cx={lab.px} cy={lab.py} r={lab.r} className={styles.marker} style={{ stroke: fuelColor(s.fuel) }} />
            <text x={lab.px} y={lab.py + 4} textAnchor="middle" className={styles.markerLetter}>{fuelLetter(s.fuel)}</text>
            <rect x={lab.x - lab.w / 2} y={lab.y - 8} width={lab.w} height="16" rx="2" className={styles.etaBg} />
            {lab.level && (
              <g transform={`translate(${lab.x - lab.w / 2 + 3},${lab.y - 6})`}><LevelGlyph level={lab.level} size={12} /></g>
            )}
            <text x={lab.x + (lab.level ? 8 : 0)} y={lab.y + 4} textAnchor="middle" className={styles.etaLabel}>{lab.text}</text>
          </g>
        );
      })}
    </svg>
  );
}

function FuelBox({ x, y, fuel }: { x: number; y: number; fuel: string }) {
  return (
    <g transform={`translate(${x},${y})`} aria-hidden="true">
      <rect width="16" height="16" rx="2" className={styles.fuelBox} style={{ stroke: fuelColor(fuel) }} />
      <text x="8" y="12" textAnchor="middle" className={styles.fuelLetter}>{fuelLetter(fuel)}</text>
    </g>
  );
}

function etaText(s: Shipment, simTime: string, nowTick: number, tm: number, narrow: boolean): string {
  const clock = s.arrivalTick != null && simTime ? tickClock(simTime, nowTick, s.arrivalTick, tm) : null;
  if (s.pending) return clock && !narrow ? `submitted, arrives ${clock}` : 'submitted';
  if (clock) return narrow ? clock : `arrives ${clock}`;
  return s.remaining <= 0 ? 'arriving' : `in ${s.remaining} ${s.remaining === 1 ? 'tick' : 'ticks'}`;
}
