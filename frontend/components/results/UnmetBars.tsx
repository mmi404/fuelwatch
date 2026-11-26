import { Cell, POLICY_NAME, level, litres, whole } from './data';
import s from './Results.module.css';

export interface BarRow { key: string; name: string; cell: Cell; tone: 'policy' | 'nothing'; tag?: string }

const detail = (c: Cell) =>
  `${POLICY_NAME[c.policy]}: ${litres(c.unmet_liters)} unmet, service level ${level(c.service_level)}, ${whole(c.shipments)} shipments, ${litres(c.litres_shipped)} shipped`;

/**
 * Unmet litres as bars from zero on a scale the caller chooses, so several of these can share one. The litres and the
 * service level are printed on every row, so the bar is only ever a second way to read a number that is already there.
 */
export default function UnmetBars({ rows, end, caption }: { rows: BarRow[]; end: number; caption: string }) {
  return (
    <table className={s.bars}>
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Policy</th>
          <th scope="col" className={s.trackHead}>
            <span aria-hidden="true">Unmet demand, 0 to {litres(end)}</span>
            <span className="sr-only">Bar</span>
          </th>
          <th scope="col" className={s.r}>Unmet</th>
          <th scope="col" className={s.r}><abbr title="Service level: the simulator's own metric">Service</abbr></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const value = r.cell.unmet_liters ?? 0;
          return (
            <tr key={r.key} title={detail(r.cell)}>
              <th scope="row">
                {r.name}
                {r.tag && <span className={s.tag}>{r.tag}</span>}
              </th>
              <td className={s.trackCell} aria-hidden="true">
                <span className={s.track}>
                  {value >= 0.5 && <span className={s.bar} data-tone={r.tone} style={{ width: `${Math.min(100, (value / end) * 100)}%` }} />}
                </span>
              </td>
              <td className={s.value}>{litres(r.cell.unmet_liters)}</td>
              <td className={s.value}>{level(r.cell.service_level)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
