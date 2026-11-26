'use client';

import type { World } from '@/lib/api';
import { LevelGlyph, levelLabel } from '@/components/ui/Level';
import { liters, tickHours } from '@/lib/format';
import { pairKey } from '@/lib/risk';
import { fmtH, shortName } from './layout';
import type { StationView } from './layout';
import type { Hot, Hover } from './NetworkSvg';
import styles from './NetworkMap.module.css';

/** Depot x station matrix: the text alternative to the diagram and its second way in. Same hover state as the diagram. */
export default function Matrix({
  world,
  depotIds,
  stationIds,
  views,
  hot,
  setHover,
  select,
}: {
  world: World;
  depotIds: string[];
  stationIds: string[];
  views: Map<string, StationView>;
  hot: Hot | null;
  setHover: (h: Hover | null) => void;
  select: (key: string | null) => void;
}) {
  const tm = world.instance.tick_minutes;
  const depots = depotIds.map((id) => world.depots.find((d) => d.id === id)!);
  const stations = stationIds.map((id) => world.stations.find((s) => s.id === id)!);
  const route = (d: string, s: string) => world.routes.find((r) => r.source_depot_id === d && r.destination_station_id === s);

  return (
    <div className={styles.matrixWrap}>
      <table className={styles.matrix}>
        <caption className={styles.matrixCap}>
          <span className="label">Depot to station routes</span>
          <span className={styles.capNote}>transit time and largest single shipment</span>
        </caption>
        <thead>
          <tr>
            <th scope="col" className={styles.corner}><span className="sr-only">Depot</span></th>
            {stations.map((s) => {
              const worst = views.get(s.id)?.worst ?? null;
              const level = s.status !== 'OPEN' ? 'OUTAGE' : worst?.level ?? 'UNKNOWN';
              const hotCol = hot?.stations.has(s.id);
              return (
                <th
                  key={s.id}
                  scope="col"
                  className={hotCol ? styles.hotHead : undefined}
                  onMouseEnter={() => setHover({ kind: 'station', id: s.id })}
                  onMouseLeave={() => setHover(null)}
                >
                  <button
                    type="button"
                    className={styles.headBtn}
                    disabled={!worst}
                    onClick={() => worst && select(pairKey(worst.station_id, worst.fuel_type))}
                    onFocus={() => setHover({ kind: 'station', id: s.id })}
                    onBlur={() => setHover(null)}
                    title={worst ? `Open the worst pair at ${s.name}` : s.name}
                  >
                    <span className={styles.headName}>{shortName(s.name)}</span>
                    <span className={styles.headWord}>
                      <LevelGlyph level={level} size={12} />
                      {levelLabel(level)}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {depots.map((d) => {
            const hotRow = hot?.depots.has(d.id);
            return (
              <tr key={d.id} className={hotRow ? styles.hotRow : undefined}>
                <th
                  scope="row"
                  className={hotRow ? styles.hotHead : undefined}
                  onMouseEnter={() => setHover({ kind: 'depot', id: d.id })}
                  onMouseLeave={() => setHover(null)}
                >
                  <span className={styles.rowName}>{shortName(d.name)}</span>
                  {d.status !== 'OPEN' && (
                    <span className={styles.rowStatus}>
                      <LevelGlyph level="OUTAGE" size={12} />
                      outage
                    </span>
                  )}
                </th>
                {stations.map((s) => {
                  const r = route(d.id, s.id);
                  const hotCol = hot?.stations.has(s.id);
                  if (!r) {
                    return (
                      <td key={s.id} className={`${styles.cell} ${styles.none} ${hotCol ? styles.hotCol : ''}`}>
                        <span aria-hidden="true">—</span>
                        <span className="sr-only">no route</span>
                      </td>
                    );
                  }
                  const closed = r.status !== 'AVAILABLE';
                  const on = hot?.routes.has(r.id);
                  return (
                    <td
                      key={s.id}
                      className={`${styles.cell} ${on ? styles.hotCell : ''} ${hotCol ? styles.hotCol : ''}`}
                      onMouseEnter={() => setHover({ kind: 'route', id: r.id })}
                      onMouseLeave={() => setHover(null)}
                    >
                      <span className={closed ? styles.struck : undefined}>
                        <span className={styles.cellMain}>{fmtH(tickHours(r.transit_ticks, tm))}</span>
                        <span className={styles.cellSub}>{liters(r.max_shipment)}</span>
                      </span>
                      {closed && <span className={styles.closedWord}>closed</span>}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
