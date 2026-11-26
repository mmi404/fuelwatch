'use client';

import { memo, useCallback, useMemo, useRef, useState } from 'react';
import Sparkline from '@/components/charts/Sparkline';
import { LevelChip, LevelGlyph, levelLabel } from '@/components/ui/Level';
import { FuelTag } from '@/components/ui/Fuel';
import { Panel } from '@/components/ui/Panel';
import type { Risk } from '@/lib/api';
import { fuelName, hours, int, pct, pretty } from '@/lib/format';
import { FUELS, levelColor, pairKey, rankRisks, riskKey } from '@/lib/risk';
import type { StockPoint } from '@/lib/world';
import { useLive } from '@/lib/world';
import { AXIS_H, BANDS, TICKS_H, axisPct, coverFigure, coverShape, levelSummary } from './coverModel';
import styles from './CoverBoard.module.css';
import { TipLine, useTip } from './Tip';

type Sort = 'worst' | 'station';
type FuelFilter = 'ALL' | 'DIESEL' | 'PETROL' | 'OCTANE';
type ShowTip = (title: string, lines: TipLine[], x: number, y: number) => void;

const TOTAL = 12;
const FILTERS: { id: FuelFilter; label: string }[] = [
  { id: 'ALL', label: 'All' },
  { id: 'DIESEL', label: 'Diesel' },
  { id: 'PETROL', label: 'Petrol' },
  { id: 'OCTANE', label: 'Octane' },
];

/** Increments when `v` changes after the first render, so only a cell whose value changed re-runs the flash. */
function useFlashKey(v: string): number {
  const r = useRef({ v, n: 0 });
  if (r.current.v !== v) r.current = { v, n: r.current.n + 1 };
  return r.current.n;
}

function Segmented<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { id: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className={styles.segWrap}>
      <span className="label" aria-hidden="true">{label}</span>
      <div className={styles.seg} role="group" aria-label={label}>
        {options.map((o) => (
          <button key={o.id} type="button" aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The shared axis drawn behind a row's bar: status tints with hairlines. */
function Bands() {
  return (
    <>
      {BANDS.map((b) => (
        <span
          key={b.level}
          className={`${styles.band} ${b.to === AXIS_H ? styles.bandEnd : ''}`}
          style={{ left: `${axisPct(b.from)}%`, width: `${axisPct(b.to) - axisPct(b.from)}%`, ['--c' as string]: levelColor(b.level) }}
        />
      ))}
    </>
  );
}

function CoverMark({ risk }: { risk: Risk }) {
  const shape = coverShape(risk);
  const color = levelColor(risk.level);
  if (shape.kind === 'unknown') return <span className={styles.hatch} />;
  if (shape.kind === 'empty') return <span className={styles.emptyMark} style={{ ['--c' as string]: color }} />;
  if (shape.kind === 'beyond') return <span className={styles.beyond} />;
  return (
    <>
      <span
        className={`${styles.bar} ${shape.open ? styles.barOpen : ''}`}
        style={{ left: `${shape.start}%`, width: `${Math.max(shape.end - shape.start, 0)}%`, ['--c' as string]: color }}
      />
      {shape.expected != null && <span className={styles.expected} style={{ left: `${shape.expected}%` }} />}
    </>
  );
}

interface RowProps {
  risk: Risk;
  hist: StockPoint[] | undefined;
  selected: boolean;
  tabbable: boolean;
  show: ShowTip;
  hide: () => void;
  onSelect: (k: string) => void;
  onFocusRow: (k: string) => void;
  onKey: (e: React.KeyboardEvent<HTMLTableRowElement>, k: string) => void;
  setRef: (k: string, el: HTMLTableRowElement | null) => void;
}

function tipFor(r: Risk): { title: string; lines: TipLine[] } {
  const title = `${pretty(r.station_id)} ${fuelName(r.fuel_type).toLowerCase()}`;
  const fig = coverFigure(r);
  if (r.level === 'UNKNOWN') return { title, lines: [{ value: 'no data', label: 'no stockout estimate for this pair' }] };
  const lines: TipLine[] = [
    { value: fig.primary, label: 'expected time to stockout' },
    { value: r.hours_p90 === 0 ? 'now' : hours(r.hours_p90), label: 'cautious time to stockout (P90 demand)' },
    { value: `${int(r.inventory)} L`, label: `on hand, ${pct(r.inventory / r.capacity, 0)} of ${int(r.capacity)} L capacity` },
    { value: `${int(r.inbound)} L`, label: 'inbound' },
  ];
  return { title, lines };
}

const Row = memo(function Row({ risk: r, hist, selected, tabbable, show, hide, onSelect, onFocusRow, onKey, setRef }: RowProps) {
  const key = riskKey(r);
  const fig = coverFigure(r);
  const fLevel = useFlashKey(r.level);
  const fStock = useFlashKey(String(Math.round(r.inventory)));
  const fCover = useFlashKey(`${r.hours_p50 ?? 'n'}|${r.hours_p90 ?? 'n'}|${r.inventory <= 0}`);
  const fInbound = useFlashKey(String(Math.round(r.inbound)));
  const values = useMemo(() => (hist ?? []).map((p) => p.inventory), [hist]);
  const ticks = useMemo(() => (hist ?? []).map((p) => p.tick), [hist]);
  const name = pretty(r.station_id);
  const fuel = fuelName(r.fuel_type).toLowerCase();
  const fill = r.capacity > 0 ? Math.min(Math.max(r.inventory / r.capacity, 0), 1) : 0;

  const barTip = (e: React.PointerEvent) => {
    const t = tipFor(r);
    show(t.title, t.lines, e.clientX, e.clientY);
  };
  const onFocus = (e: React.FocusEvent<HTMLTableRowElement>) => {
    onFocusRow(key);
    if (e.target === e.currentTarget && e.currentTarget.matches(':focus-visible')) {
      const bar = e.currentTarget.querySelector<HTMLElement>('[data-bar]');
      if (bar) {
        const b = bar.getBoundingClientRect();
        const t = tipFor(r);
        show(t.title, t.lines, b.left + b.width / 2, b.top);
      }
    }
  };

  return (
    <tr
      ref={(el) => setRef(key, el)}
      className={`${styles.row} ${selected ? styles.selected : ''}`}
      data-pair={key}
      data-level={r.level}
      tabIndex={tabbable ? 0 : -1}
      aria-selected={selected}
      onClick={() => onSelect(key)}
      onKeyDown={(e) => onKey(e, key)}
      onFocus={onFocus}
      onBlur={hide}
    >
      <td key={`l${fLevel}`} className={`${styles.cLevel} ${fLevel ? 'flash' : ''}`}>
        <span className={styles.lvChip}><LevelChip level={r.level} /></span>
        <span className={styles.lvGlyph}>
          <LevelGlyph level={r.level} size={16} />
          <span className="sr-only">{levelLabel(r.level)}</span>
        </span>
      </td>
      <td className={styles.cStation}>{name}</td>
      <td className={styles.cFuel}>
        <span className={styles.fuelFull}><FuelTag fuel={r.fuel_type} /></span>
        <span className={styles.fuelShort}><FuelTag fuel={r.fuel_type} short /></span>
      </td>
      <td key={`s${fStock}`} className={`${styles.cStock} ${fStock ? 'flash' : ''}`}>
        <div className={styles.stock}>
          <span className="num">{int(r.inventory)}<span className="unit">L</span></span>
          <span className={styles.meter} role="img" aria-label={`${pct(fill, 0)} of ${int(r.capacity)} L capacity`}><i style={{ width: `${fill * 100}%` }} /></span>
        </div>
      </td>
      <td
        key={`b${fCover}`}
        className={`${styles.cBar} ${fCover ? 'flash' : ''}`}
        onPointerMove={barTip}
        onPointerLeave={hide}
      >
        <div className={styles.axis} data-bar aria-hidden="true">
          <Bands />
          <CoverMark risk={r} />
        </div>
      </td>
      <td key={`f${fCover}`} className={`${styles.cFig} ${fCover ? 'flash' : ''}`}>
        <div className={styles.fig}>
          <span className={`${styles.fig1} ${fig.empty ? styles.figEmpty : ''}`}>
            <span className="sr-only">Expected </span>{fig.primary}
          </span>
          <span className={styles.fig2}>{fig.secondary}</span>
        </div>
      </td>
      <td key={`i${fInbound}`} className={`${styles.cInbound} ${fInbound ? 'flash' : ''}`}>
        <span className={`num ${r.inbound > 0 ? '' : 'faint'}`}>{int(r.inbound)}<span className="unit">L</span></span>
      </td>
      <td className={styles.cTrend}>
        <Sparkline
          values={values}
          xs={ticks}
          label={`${name} ${fuel} stock`}
          format={(v) => `${int(v)} L`}
          xLabel={(t) => `tick ${t}`}
          xName="Tick"
          yName="Stock"
          onPoint={(p) => {
            if (!p) return hide();
            show(`${name} ${fuel} stock`, [{ value: `${int(p.value)} L`, label: `tick ${p.x}, since page load` }], p.clientX, p.clientY);
          }}
        />
      </td>
    </tr>
  );
});

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: TOTAL }, (_, i) => (
        <tr key={i} className={`${styles.row} ${styles.skelRow}`} aria-hidden="true">
          <td className={styles.cLevel}><span className={styles.skel} style={{ width: 22 }} /></td>
          <td className={styles.cStation}><span className={styles.skel} style={{ width: 70 }} /></td>
          <td className={styles.cFuel}><span className={styles.skel} style={{ width: 54 }} /></td>
          <td className={styles.cStock}><span className={styles.skel} style={{ width: 56, marginLeft: 'auto' }} /></td>
          <td className={styles.cBar}><div className={styles.axis}><Bands /></div></td>
          <td className={styles.cFig}><span className={styles.skel} style={{ width: 44, marginLeft: 'auto' }} /></td>
          <td className={styles.cInbound}><span className={styles.skel} style={{ width: 36, marginLeft: 'auto' }} /></td>
          <td className={styles.cTrend}><span className={styles.skel} style={{ width: 64 }} /></td>
        </tr>
      ))}
    </>
  );
}

/** The cover board: all station-fuel pairs ranked on one shared 0 to 24 h axis. */
export default function CoverBoard() {
  const { status, risks, history, selected, select, error } = useLive();
  const [sort, setSort] = useState<Sort>('worst');
  const [fuelFilter, setFuelFilter] = useState<FuelFilter>('ALL');
  const [active, setActive] = useState<string | null>(null);
  const rows = useRef(new Map<string, HTMLTableRowElement>());
  const { ref: tipRef, show, hide } = useTip();

  const loading = !status && risks.length === 0 && !error;
  const stale = status?.mode === 'STALE';

  const shown = useMemo(() => {
    const f = fuelFilter === 'ALL' ? risks : risks.filter((r) => r.fuel_type === fuelFilter);
    if (sort === 'worst') return rankRisks(f);
    return [...f].sort((a, b) => a.station_id.localeCompare(b.station_id) || FUELS.indexOf(a.fuel_type as (typeof FUELS)[number]) - FUELS.indexOf(b.fuel_type as (typeof FUELS)[number]));
  }, [risks, sort, fuelFilter]);

  const keys = useMemo(() => shown.map(riskKey), [shown]);
  const tabKey = active && keys.includes(active) ? active : selected && keys.includes(selected) ? selected : keys[0] ?? null;

  const setRef = useCallback((k: string, el: HTMLTableRowElement | null) => {
    if (el) rows.current.set(k, el);
    else rows.current.delete(k);
  }, []);
  const move = useCallback(
    (k: string, to: number) => {
      const i = Math.min(Math.max(to, 0), keys.length - 1);
      const target = keys[i];
      if (target && target !== k) {
        setActive(target);
        rows.current.get(target)?.focus();
      }
    },
    [keys],
  );
  const onKey = useCallback(
    (e: React.KeyboardEvent<HTMLTableRowElement>, k: string) => {
      if (e.target !== e.currentTarget || e.ctrlKey || e.metaKey || e.altKey) return;
      const i = keys.indexOf(k);
      if (e.key === 'ArrowDown') { e.preventDefault(); move(k, i + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(k, i - 1); }
      else if (e.key === 'Home') { e.preventDefault(); move(k, 0); }
      else if (e.key === 'End') { e.preventDefault(); move(k, keys.length - 1); }
      else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(k); }
      else if (e.key === 'Escape') select(null);
    },
    [keys, move, select],
  );

  const summary = levelSummary(shown);
  const total = risks.length || TOTAL;

  return (
    <Panel title="Cover board" meta={loading ? `of ${TOTAL} pairs` : `${shown.length} of ${total} pairs`} flush className={styles.panel}>
      <div className={styles.board} data-stale={stale ? 'true' : undefined}>
        <div className={styles.toolbar}>
          <Segmented<Sort> label="Sort" value={sort} options={[{ id: 'worst', label: 'Worst first' }, { id: 'station', label: 'By station' }]} onChange={setSort} />
          <Segmented<FuelFilter> label="Fuel" value={fuelFilter} options={FILTERS} onChange={setFuelFilter} />
        </div>
        {error && risks.length > 0 && (
          <p className={styles.notice} role="status">Last refresh failed ({error}). Showing the last data received; retrying every 5 s.</p>
        )}
        {error && risks.length === 0 ? (
          <p className={styles.empty} role="status">No data yet. The console cannot reach the backend ({error}). Retrying every 5 s.</p>
        ) : !loading && risks.length === 0 ? (
          <p className={styles.empty}>The backend reports no station-fuel pairs.</p>
        ) : (
          <div className="dimmable">
            <table className={styles.table} role="grid" aria-label={loading ? 'Cover board, loading' : `Cover board, ${summary}`} aria-busy={loading} aria-rowcount={shown.length || TOTAL}>
              <caption className="sr-only">
                {sort === 'worst' ? 'Station-fuel pairs, worst first: severity, then earliest plausible stockout.' : 'Station-fuel pairs, by station.'} Hours to stockout are drawn on one shared axis from 0 to {AXIS_H} hours. Up and down arrows move between rows, Enter selects, Escape clears.
              </caption>
              <colgroup>
                <col className={styles.colLevel} />
                <col className={styles.colStation} />
                <col className={styles.colFuel} />
                <col className={styles.colStock} />
                <col />
                <col className={styles.colFig} />
                <col className={styles.colInbound} />
                <col className={styles.colTrend} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col" className={styles.hLevel}>Level</th>
                  <th scope="col">Station</th>
                  <th scope="col" className={styles.hFuel}>Fuel</th>
                  <th scope="col" className={styles.r}>Stock</th>
                  <th scope="col" className={styles.axisTh}>
                    <span className="sr-only">Hours to stockout, axis 0 to {AXIS_H} hours</span>
                    <div className={styles.axisHead} aria-hidden="true">
                      {BANDS.map((b) => (
                        <span key={b.level} className={styles.bandName} style={{ left: `${(axisPct(b.from) + axisPct(b.to)) / 2}%` }}>{b.name}</span>
                      ))}
                      {TICKS_H.map((t, i) => (
                        <span key={t} className={`${styles.tick} ${i === 0 ? styles.tickFirst : i === TICKS_H.length - 1 ? styles.tickLast : ''}`} style={{ left: `${axisPct(t)}%` }}>
                          {t}{i === TICKS_H.length - 1 ? ' h' : ''}
                        </span>
                      ))}
                    </div>
                  </th>
                  <th scope="col" className={styles.r}>Expected</th>
                  <th scope="col" className={styles.r}>Inbound</th>
                  <th scope="col"><span className="sr-only">Stock </span>Trend</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <SkeletonRows />
                ) : (
                  shown.map((r) => {
                    const k = riskKey(r);
                    return (
                      <Row
                        key={k}
                        risk={r}
                        hist={history[pairKey(r.station_id, r.fuel_type)]}
                        selected={selected === k}
                        tabbable={tabKey === k}
                        show={show}
                        hide={hide}
                        onSelect={select}
                        onFocusRow={setActive}
                        onKey={onKey}
                        setRef={setRef}
                      />
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div ref={tipRef} className={styles.tip} role="presentation" hidden />
    </Panel>
  );
}
