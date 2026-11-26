'use client';

import { useLive } from '@/lib/world';
import css from './AlertFeed.module.css';

const STATE_LABEL = { overdue: 'Overdue', arrived_late: 'Arrived late', lost: 'Fuel lost' } as const;

/** A clock for a late delivery, a drop for lost fuel. The label beside the name says the same in words. */
function CheckGlyph({ lost }: { lost: boolean }) {
  return (
    <svg className={css.anomalyGlyph} width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      {lost ? <path d="M8 1.5C8 1.5 3 7 3 10a5 5 0 0 0 10 0C13 7 8 1.5 8 1.5Z" /> : <><circle cx="8" cy="8" r="6.5" /><path d="M8 4.5V8l2.5 1.5" /></>}
    </svg>
  );
}

/** Late supply deliveries and fuel lost on arrival (design 6.5). Draws nothing when there are none. */
export default function DeliveryChecks() {
  const { deliveryChecks, lastOkAt } = useLive();
  if (!lastOkAt || deliveryChecks.length === 0) return null;
  return (
    <section aria-labelledby="alerts-deliveries" className="dimmable">
      <h3 id="alerts-deliveries" className={`label ${css.sect}`}>Delivery problems</h3>
      <ul>
        {deliveryChecks.map((f) => (
          <li key={f.id} className={css.anomaly}>
            <CheckGlyph lost={f.kind === 'lost_fuel'} />
            <span className={css.anomalyBody}>
              <span className={css.anomalyHead}>
                <span className={css.anomalyName}>{f.place_name.replace(/ (Station|Depot)$/, '')}</span>
                <span className="label">{STATE_LABEL[f.state]}</span>
              </span>
              <span className={css.anomalyText}>{f.summary}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
