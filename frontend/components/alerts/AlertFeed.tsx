'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Alert, Level } from '@/lib/api';
import { LevelChip, LevelGlyph, levelLabel } from '@/components/ui/Level';
import { FuelTag } from '@/components/ui/Fuel';
import { Panel } from '@/components/ui/Panel';
import { fuelName, plural, pretty } from '@/lib/format';
import { pairKey } from '@/lib/risk';
import { useLive } from '@/lib/world';
import Anomalies from './Anomalies';
import DeliveryChecks from './DeliveryChecks';
import { Cause, StationEntry, buildCauses, buildStations, leftText, openAlerts, openFor } from './model';
import css from './AlertFeed.module.css';

const CAP = 6;

type Item = { kind: 'cause'; key: string; hideKey: string; worst: Level; cause: Cause } | { kind: 'station'; key: string; hideKey: string; worst: Level; station: StationEntry };

const coverText = (h: number | null) => (h == null ? 'no estimate' : h <= 0 ? 'now' : leftText(h));

/** One fuel letter tile with its level glyph. The word is read out by screen readers. */
function FuelLevel({ alert }: { alert: Alert }) {
  return (
    <span className={css.fuelLevel}>
      <FuelTag fuel={alert.fuel_type} short />
      <LevelGlyph level={alert.level} size={13} />
      <span className="sr-only">{`${fuelName(alert.fuel_type)} ${levelLabel(alert.level)}, cover ${coverText(alert.hours_p90)}`}</span>
    </span>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className={css.skel}>
          <span className={css.bar} style={{ width: '38%' }} />
          <span className={css.bar} style={{ width: `${62 - i * 12}%`, height: 10 }} />
        </div>
      ))}
    </div>
  );
}

export default function AlertFeed() {
  const { status, snapshot, alerts, risks, error, lastOkAt, selected, select } = useLive();
  const [expanded, setExpanded] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [showHidden, setShowHidden] = useState(false);
  const [flashing, setFlashing] = useState<ReadonlySet<string>>(new Set());
  const seen = useRef<Set<string> | null>(null);

  const open = useMemo(() => openAlerts(alerts), [alerts]);
  const tick = status?.tick ?? snapshot?.world.instance.tick ?? 0;
  const tm = status?.tick_minutes ?? snapshot?.world.instance.tick_minutes ?? 15;

  const items = useMemo<Item[]>(() => {
    if (!snapshot) return [];
    const causes = buildCauses(snapshot, open, tick, tm);
    const stations = buildStations(snapshot, open, causes);
    return [
      ...causes.map((cause): Item => ({ kind: 'cause', key: `cause:${cause.key}`, hideKey: `cause:${cause.key}|${cause.worst}`, worst: cause.worst, cause })),
      ...stations.map((station): Item => ({ kind: 'station', key: `station:${station.stationId}`, hideKey: `station:${station.stationId}|${station.worst}`, worst: station.worst, station })),
    ];
  }, [snapshot, open, tick, tm]);

  // Flash only entries that were not on screen before. The first snapshot and a reconnect baseline never flash.
  const keyList = items.map((i) => i.key).join('|');
  useEffect(() => {
    if (!lastOkAt) return;
    const now = new Set(keyList ? keyList.split('|') : []);
    if (seen.current === null) {
      seen.current = now;
      return;
    }
    const fresh = [...now].filter((k) => !seen.current!.has(k));
    seen.current = now;
    if (!fresh.length) return;
    setFlashing(new Set(fresh));
    const t = setTimeout(() => setFlashing(new Set()), 800);
    return () => clearTimeout(t);
  }, [keyList, lastOkAt]);

  const loading = !lastOkAt && !error;
  const failed = !lastOkAt && !!error;
  const stale = status?.mode === 'STALE';
  const hiddenCount = items.filter((i) => hidden.has(i.hideKey)).length;
  const visible = items.filter((i) => showHidden || !hidden.has(i.hideKey));
  const shown = expanded ? visible : visible.slice(0, CAP);
  const causesShown = shown.filter((i): i is Extract<Item, { kind: 'cause' }> => i.kind === 'cause');
  const stationsShown = shown.filter((i): i is Extract<Item, { kind: 'station' }> => i.kind === 'station');

  const hide = (i: Item) => setHidden((h) => new Set(h).add(i.hideKey));
  const unhide = (i: Item) =>
    setHidden((h) => {
      const n = new Set(h);
      n.delete(i.hideKey);
      return n;
    });

  const renderRow = (item: Item) => {
    const isHidden = hidden.has(item.hideKey);
    const list = item.kind === 'cause' ? item.cause.alerts : item.station.alerts;
    const worstAlert = list[0];
    const isSelected = selected != null && list.some((a) => pairKey(a.station_id, a.fuel_type) === selected);
    const name = item.kind === 'cause' ? item.cause.title : item.station.name.replace(/ Station$/, '');
    return (
      <li key={item.key} className={`${css.row}${flashing.has(item.key) ? ' flash' : ''}`} data-selected={isSelected || undefined} data-hidden={isHidden || undefined}>
        <button type="button" className={css.main} aria-current={isSelected || undefined} title={`Select ${pairLabel(worstAlert)}`} onClick={() => select(pairKey(worstAlert.station_id, worstAlert.fuel_type))}>
          <span className={css.line}>
            {item.kind === 'cause' ? <LevelGlyph level={item.cause.closure ? 'OUTAGE' : item.cause.worst} size={14} /> : <LevelChip level={item.station.worst} />}
            <span className={`${css.name}${item.kind === 'cause' ? ` ${css.nameWrap}` : ''}`} title={item.kind === 'station' ? item.station.name : undefined}>{name}</span>
            <span className={`${css.when} num`}>
              {item.kind === 'cause' ? plural(item.cause.alerts.length, 'pair') : coverText(item.station.hours)}
              <span className="unit">{item.kind === 'cause' ? 'on alert' : 'cautious'}</span>
            </span>
          </span>
          {item.kind === 'cause' ? <CauseBody cause={item.cause} /> : <StationBody station={item.station} tm={tm} tick={tick} />}
          <span className="sr-only">Selects {pairLabel(worstAlert)}.</span>
        </button>
        <span className={css.side}>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => (isHidden ? unhide(item) : hide(item))}
            aria-label={`${isHidden ? 'Show again' : 'Hide for this session'}: ${name}. Local to this browser, the backend is not told.`}
            title="Local to this browser. The backend is not told, and it comes back if it gets worse."
          >
            {isHidden ? 'Unhide' : 'Hide'}
          </button>
        </span>
      </li>
    );
  };

  const meta = loading ? '--' : `${open.length} open`;

  return (
    <div data-stale={stale ? 'true' : undefined}>
      <Panel title="Open alerts" meta={meta} flush>
        <Anomalies />
        <DeliveryChecks />
        {loading && <Skeleton />}
        {failed && (
          <div className={css.empty}>
            <p className={css.emptyHead}>Alerts unavailable</p>
            <p className="muted">The backend did not answer ({error}). Retrying every 5 s.</p>
          </div>
        )}
        {!loading && !failed && open.length === 0 && (
          <div className={`${css.empty} dimmable`}>
            <span className={css.okRow}>
              <LevelGlyph level="OK" size={16} />
              <span className={css.emptyHead}>No open alerts</span>
            </span>
            <p className="muted">
              Stock cover is above its alert thresholds for all {risks.length || 12} pairs.
            </p>
          </div>
        )}
        {!loading && !failed && open.length > 0 && (
          <div className="dimmable">
            {causesShown.length > 0 && (
              <section aria-labelledby="alerts-causes">
                <h3 id="alerts-causes" className={`label ${css.sect}`}>Disruptions</h3>
                <ul>{causesShown.map(renderRow)}</ul>
              </section>
            )}
            {stationsShown.length > 0 && (
              <section aria-labelledby="alerts-stations">
                <h3 id="alerts-stations" className={`label ${css.sect}`}>Stations</h3>
                <ul>{stationsShown.map(renderRow)}</ul>
              </section>
            )}
            {shown.length === 0 && <p className={`${css.empty} muted`}>All entries are hidden in this browser.</p>}
            {(visible.length > CAP || hiddenCount > 0) && (
              <div className={css.foot}>
                {visible.length > CAP && (
                  <button type="button" className="btn sm" aria-expanded={expanded} onClick={() => setExpanded((e) => !e)}>
                    {expanded ? `Show top ${CAP}` : `Show all (${visible.length})`}
                  </button>
                )}
                {hiddenCount > 0 && (
                  <span className={css.hiddenNote}>
                    <span className="muted">{hiddenCount} hidden, local to this browser</span>
                    <button type="button" className="btn ghost sm" aria-pressed={showHidden} onClick={() => setShowHidden((s) => !s)}>
                      {showHidden ? 'Hide again' : 'Show'}
                    </button>
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}

const pairLabel = (a: Alert) => `${fuelName(a.fuel_type)} at ${a.station_id.replace(/^station-/, '')}`;

function CauseBody({ cause }: { cause: Cause }) {
  const byStation = new Map<string, Alert[]>();
  for (const a of cause.alerts) byStation.set(a.station_id, [...(byStation.get(a.station_id) ?? []), a]);
  return (
    <>
      {cause.window && <span className={`${css.sub} mono`}>{cause.window}</span>}
      <span className={css.scope}>
        {[...byStation].map(([sid, list]) => (
          <span key={sid} className={css.scopeItem}>
            <span className={css.scopeName}>{pretty(sid)}</span>
            <span className={css.fuels}>{list.map((a) => <FuelLevel key={a.id} alert={a} />)}</span>
          </span>
        ))}
      </span>
    </>
  );
}

function StationBody({ station, tick, tm }: { station: StationEntry; tick: number; tm: number }) {
  return (
    <>
      <span className={css.sub}>
        <span className={css.fuels}>{station.alerts.map((a) => <FuelLevel key={a.id} alert={a} />)}</span>
        <span className={css.age}>open for <span className="mono">{openFor(tick - station.firstTick, tm)}</span></span>
      </span>
      {station.cause && (
        <span className={css.cause}>
          <span className="label">Likely cause</span> {station.cause.short}
          {station.cause.window ? `, ${station.cause.window}` : ''}
          {station.moreCauses > 0 ? `, and ${station.moreCauses} more` : ''}
        </span>
      )}
    </>
  );
}
