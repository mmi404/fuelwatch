'use client';

import { openFor } from './model';
import { useLive } from '@/lib/world';
import css from './AlertFeed.module.css';

/** A diamond with a question mark for "no known cause", a circle with an i for "cause known". */
function KindGlyph({ unexplained }: { unexplained: boolean }) {
  return (
    <svg className={css.anomalyGlyph} width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      {unexplained ? <path d="M8 1 15 8 8 15 1 8Z" /> : <circle cx="8" cy="8" r="6.5" />}
      <text x="8" y="11.2" textAnchor="middle" fontSize="9" fontWeight="700" fill="currentColor" stroke="none">{unexplained ? '?' : 'i'}</text>
    </svg>
  );
}

/** Demand that departs from the forecast (design 6.5). Draws nothing when there is none. */
export default function Anomalies() {
  const { anomalies, status, lastOkAt } = useLive();
  if (!lastOkAt || anomalies.length === 0) return null;
  const tick = status?.tick ?? 0;
  const tm = status?.tick_minutes ?? 15;
  return (
    <section aria-labelledby="alerts-anomalies" className="dimmable">
      <h3 id="alerts-anomalies" className={`label ${css.sect}`}>Unusual demand</h3>
      <ul>
        {anomalies.map((a) => (
          <li key={a.station_id} className={css.anomaly}>
            <KindGlyph unexplained={a.kind === 'unexplained'} />
            <span className={css.anomalyBody}>
              <span className={css.anomalyHead}>
                <span className={css.anomalyName}>{a.station_name.replace(/ Station$/, '')}</span>
                <span className="label">{a.kind === 'unexplained' ? 'Unexplained' : 'Explained'}</span>
              </span>
              <span className={css.anomalyText}>{a.summary}</span>
              <span className={css.anomalyText}>
                first seen <span className="mono">{openFor(Math.max(0, tick - a.first_tick), tm)}</span> ago
                {a.kind === 'unexplained' ? '. New proposals for this station have lower confidence.' : ''}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
