'use client';

import { Panel } from '@/components/ui/Panel';
import type { SimAllocation, World } from '@/lib/api';
import { int, liters, pretty, tickClock } from '@/lib/format';
import { flightSummary } from './model';
import s from './Ledger.module.css';

const ORDER: { key: string; word: string }[] = [
  { key: 'PENDING', word: 'Submitted' },
  { key: 'IN_TRANSIT', word: 'In transit' },
  { key: 'ARRIVED', word: 'Arrived' },
  { key: 'FAILED', word: 'Failed' },
  { key: 'CANCELLED', word: 'Cancelled' },
];

/** The simulator's own view of what is on the road, from the snapshot allocations: litres in transit and a count per status. */
export default function InFlightStrip({ world, allocations }: { world: World | null; allocations: SimAllocation[] | null }) {
  if (allocations === null) {
    return (
      <Panel title="In flight now" meta="waiting for the first snapshot">
        <div className={s.flight} aria-busy="true">
          <span className={`${s.skel} pulse`} style={{ width: 140, height: 28 }} />
          <span className={`${s.skel} pulse`} style={{ width: 320, height: 16 }} />
        </div>
      </Panel>
    );
  }
  const f = flightSummary(allocations);
  const nowTick = world?.instance.tick ?? 0;
  const tm = world?.instance.tick_minutes ?? 15;
  const nextEta = f.next?.expected_arrival_tick ?? null;
  const inFlightCount = (f.byStatus.PENDING ?? 0) + (f.byStatus.IN_TRANSIT ?? 0);
  return (
    <Panel title="In flight now" meta={`snapshot at tick ${nowTick}`}>
      <div className={`${s.flight} dimmable`}>
        <div className={s.flightBig}>
          <span className={`mono ${s.bigFig}`}>{int(f.litersOnRoad)}</span>
          <span className={s.bigUnit}>L on the road</span>
          <span className="muted">
            {inFlightCount === 0
              ? 'Nothing is submitted or in transit.'
              : `in ${inFlightCount} ${inFlightCount === 1 ? 'shipment' : 'shipments'}${
                  nextEta != null && f.next
                    ? `. Next arrival at ${pretty(f.next.destination_station_id)}, tick ${nextEta}${nextEta > nowTick ? ` in ${(nextEta - nowTick) * tm} min (${tickClock(world?.instance.sim_time, nowTick, nextEta, tm)})` : ' (due now)'}`
                    : ''
                }`}
          </span>
        </div>
        <dl className={s.counts}>
          {ORDER.map(({ key, word }) => {
            const n = f.byStatus[key] ?? 0;
            return (
              <div key={key} className={`${s.count} ${n === 0 ? s.countZero : ''}`}>
                <dt className="label">{word}</dt>
                <dd>
                  <span className="mono">{n}</span>
                  {n > 0 && <span className={`${s.countL} mono`}>{liters(f.litersByStatus[key] ?? 0)}</span>}
                </dd>
              </div>
            );
          })}
        </dl>
      </div>
    </Panel>
  );
}
