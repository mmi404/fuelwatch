'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import type { Risk, World } from '@/lib/api';
import { FuelTag } from '@/components/ui/Fuel';
import { LevelChip, LevelGlyph, levelLabel } from '@/components/ui/Level';
import { Panel } from '@/components/ui/Panel';
import { fuelName, liters, plural, tickClock, tickHours } from '@/lib/format';
import { FUELS, levelRank, rankRisks } from '@/lib/risk';
import { useLive } from '@/lib/world';
import { computeLayout, disruptionList, fmtH, outlook, shipmentsInFlight, shortName, stationViews } from './layout';
import type { Shipment } from './layout';
import Matrix from './Matrix';
import NetworkSvg, { hotSet } from './NetworkSvg';
import type { Hover } from './NetworkSvg';
import styles from './NetworkMap.module.css';

/** Layered schematic of the supply network (depots left, stations right) with a depot x station matrix under it. */
export default function NetworkMap() {
  const { snapshot, status, risks, error, selected, select } = useLive();

  if (!snapshot) {
    return (
      <Panel title="Network" meta={error ? 'no data' : 'loading'}>
        {error ? (
          <p className={styles.message}>
            <LevelGlyph level="UNKNOWN" /> No network data yet. The console cannot reach the backend ({error}). Retrying every 5 s.
          </p>
        ) : (
          <div className={styles.skeleton} aria-busy="true">
            <span className="sr-only">Loading the network</span>
            <div className={styles.skelCol}>{[0, 1].map((i) => <span key={i} className={`${styles.skelDepot} pulse`} />)}</div>
            <div className={styles.skelCol}>{[0, 1, 2, 3].map((i) => <span key={i} className={`${styles.skelStation} pulse`} />)}</div>
          </div>
        )}
      </Panel>
    );
  }

  const stale = status?.mode === 'STALE' || snapshot.world.stale;
  return <Network world={snapshot.world} risks={risks} stale={stale} error={error} selected={selected} select={select} />;
}

function Network({
  world,
  risks,
  stale,
  error,
  selected,
  select,
}: {
  world: World;
  risks: Risk[];
  stale: boolean;
  error: string | null;
  selected: string | null;
  select: (key: string | null) => void;
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const [frame, setFrame] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(760);
  const patternId = `hatch-${useId().replace(/:/g, '')}`;

  // The viewBox is as wide as the box on screen, so text is drawn at its true size instead of being scaled down on phones.
  useEffect(() => {
    if (!frame) return;
    const measure = () => setWidth(Math.min(1040, Math.max(320, Math.round(frame.clientWidth))));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    return () => observer.disconnect();
  }, [frame]);

  const layout = useMemo(() => computeLayout(world, width), [world, width]);
  const views = useMemo(() => stationViews(world, risks), [world, risks]);
  const hot = useMemo(() => hotSet(hover, world.routes), [hover, world.routes]);

  const { tick, tick_minutes: tm, sim_time: simTime } = world.instance;
  const ships = shipmentsInFlight(world.allocations, world.routes, tick);
  const nameOf = (id: string) => {
    const node = [...world.depots, ...world.stations].find((n) => n.id === id);
    return node ? shortName(node.name) : id;
  };

  const disruptions = disruptionList(world, nameOf);

  const worst = rankRisks(risks)[0];
  const summary = [
    `Supply network: ${plural(world.depots.length, 'depot')} on the left, ${plural(world.stations.length, 'station')} on the right, ${plural(world.routes.length, 'route')}.`,
    disruptions.length ? `Disruptions: ${disruptions.join('; ')}.` : 'No disruptions.',
    ships.length ? `${plural(ships.length, 'shipment')} in flight.` : 'No shipments in flight.',
    worst ? `Worst pair: ${nameOf(worst.station_id)} ${fuelName(worst.fuel_type).toLowerCase()}, ${levelLabel(worst.level).toLowerCase()}, ${outlook(worst)}.` : '',
    'Routes and station levels are repeated in the tables below.',
  ].join(' ');

  const sizes = world.routes.map((r) => r.max_shipment);
  const maxShip = Math.max(1, ...sizes);
  const widths = sizes.length ? [...new Set([Math.min(...sizes), Math.max(...sizes)])] : [];

  const meta = error
    ? `refresh failed, tick ${tick} shown`
    : `${plural(world.depots.length, 'depot')} · ${plural(world.stations.length, 'station')} · ${plural(world.routes.length, 'route')}`;

  return (
    <Panel title="Network" meta={meta} flush>
      <div data-stale={stale ? 'true' : undefined}>
        {stale && (
          <p className={styles.banner}>
            <LevelGlyph level="UNKNOWN" />
            <strong>Stale data.</strong> <span>The network is shown as of tick {tick}.</span>
          </p>
        )}
        {disruptions.length > 0 && (
          <p className={styles.banner}>
            <LevelGlyph level="OUTAGE" />
            <strong>{plural(disruptions.length, 'disruption')}:</strong> <span>{disruptions.join(' · ')}</span>
          </p>
        )}

        <div className="dimmable">
          <div className={styles.diagram}>
            <div ref={setFrame}>
              <NetworkSvg
                world={world}
                risks={risks}
                layout={layout}
                views={views}
                hot={hot}
                setHover={setHover}
                selected={selected}
                select={select}
                label={summary}
                patternId={patternId}
              />
            </div>
          </div>

          <div className={styles.foot}>
            {ships.length === 0 ? (
              <p className={styles.noShips}>
                <svg width="36" height="20" viewBox="0 0 36 20" aria-hidden="true" focusable="false">
                  <line x1="0" y1="10" x2="36" y2="10" className={styles.sampleLine} />
                  <circle cx="18" cy="10" r="8.5" className={styles.sampleMarker} />
                </svg>
                No shipments in transit. Approve a proposal to send one.
              </p>
            ) : (
              <ul className={styles.shipList} aria-label="Shipments in flight">
                {ships.map((s) => (
                  <ShipRow key={s.id} s={s} risks={risks} from={nameOf(s.route.source_depot_id)} to={nameOf(s.route.destination_station_id)} when={whenText(s, simTime, tick, tm)} />
                ))}
              </ul>
            )}

            <div className={styles.legend}>
              <span className={styles.legendItem}>
                Line width: max shipment
                {widths.map((v) => (
                  <span key={v} className={styles.widthSample}>
                    <svg width="22" height="8" viewBox="0 0 22 8" aria-hidden="true" focusable="false">
                      <line x1="0" y1="4" x2="22" y2="4" className={styles.sampleWidth} strokeWidth={1.5 + 4.5 * (v / maxShip)} />
                    </svg>
                    {liters(v)}
                  </span>
                ))}
              </span>
              <span className={styles.legendItem}>Grey route: nothing wrong on it. Colour: carries fuel to an at-risk pair, or is disrupted.</span>
              {ships.length > 0 && <span className={styles.legendItem}>Marker: size is litres, letter is fuel, place is share of transit done. Dashed ring: destination.</span>}
              <span className={styles.legendItem}>Station hours: expected time to stockout for its worst pair, looking 24 h ahead. &gt;24 h means more than 24 h.</span>
              <span className={styles.legendItem}>Depot bars: stock from zero to capacity. Point at or focus a depot for litres.</span>
            </div>
          </div>

          <Matrix
            world={world}
            depotIds={layout.depots.map((d) => d.id)}
            stationIds={layout.stations.map((s) => s.id)}
            views={views}
            hot={hot}
            setHover={setHover}
            select={select}
          />
        </div>

        <StockTables world={world} views={views} />
      </div>
    </Panel>
  );
}

function whenText(s: Shipment, simTime: string, tick: number, tm: number): string {
  const transit = `${fmtH(tickHours(s.route.transit_ticks, tm))} transit`;
  const arrival = s.arrivalTick != null ? `arrives ${tickClock(simTime, tick, s.arrivalTick, tm)}` : null;
  if (s.pending) return `submitted, not departed, ${arrival ?? transit}`;
  return arrival ?? `${plural(Math.max(0, s.remaining), 'tick')} to go`;
}

function ShipRow({ s, risks, from, to, when }: { s: Shipment; risks: Risk[]; from: string; to: string; when: string }) {
  const risk = risks.find((r) => r.station_id === s.route.destination_station_id && r.fuel_type === s.fuel);
  const atRisk = risk && risk.level !== 'UNKNOWN' && levelRank(risk.level) >= levelRank('WATCH');
  return (
    <li className={styles.shipRow}>
      <span className="mono">{liters(s.quantity)}</span>
      <FuelTag fuel={s.fuel} />
      <span>
        {from} to {to}
      </span>
      <span className="muted">{when}</span>
      {atRisk && <LevelChip level={risk.level} label={`${levelLabel(risk.level)} pair`} />}
    </li>
  );
}

/** What the diagram shows about node contents, for screen readers. Routes are in the visible matrix, shipments in the footer. */
function StockTables({ world, views }: { world: World; views: ReturnType<typeof stationViews> }) {
  return (
    <div className="sr-only">
      <table>
        <caption>Station fuel levels</caption>
        <thead>
          <tr>
            <th scope="col">Station</th>
            <th scope="col">Status</th>
            {FUELS.map((f) => <th key={f} scope="col">{fuelName(f)}: level and time to stockout</th>)}
          </tr>
        </thead>
        <tbody>
          {world.stations.map((s) => (
            <tr key={s.id}>
              <th scope="row">{s.name}</th>
              <td>{s.status}</td>
              {FUELS.map((f) => {
                const r = views.get(s.id)?.byFuel.get(f);
                return <td key={f}>{r ? `${levelLabel(r.level)}, ${outlook(r)}` : 'no forecast'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <caption>Depot stock</caption>
        <thead>
          <tr>
            <th scope="col">Depot</th>
            <th scope="col">Status</th>
            {FUELS.map((f) => <th key={f} scope="col">{fuelName(f)}: stock of capacity</th>)}
          </tr>
        </thead>
        <tbody>
          {world.depots.map((d) => (
            <tr key={d.id}>
              <th scope="row">{d.name}</th>
              <td>{d.status}</td>
              {FUELS.map((f) => <td key={f}>{`${liters(d.inventory[f] ?? 0)} of ${liters(d.capacity[f] ?? 0)}`}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
