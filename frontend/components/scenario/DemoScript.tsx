import Link from 'next/link';
import { Panel } from '@/components/ui/Panel';
import s from './Scenario.module.css';

interface Step { title: string; body: React.ReactNode; go?: { href: string; label: string } }

const STEPS: Step[] = [
  {
    title: 'Start the clock',
    body: 'Reset first, then press Run. At the default speed of about one tick a second, the first proposal appears after about 45 seconds.',
    go: { href: '#sim-control', label: 'Controls' },
  },
  {
    title: 'Pause and read it',
    body: 'Press Pause. On the overview, open the top row of the cover board: hours left, the stock forecast, and what happens if nothing is sent.',
    go: { href: '/', label: 'Overview' },
  },
  {
    title: 'Approve it',
    body: 'In the proposal queue, press the button that names the litres (“Dispatch … L”). The server checks fresh data first and can only lower the amount.',
    go: { href: '/', label: 'Proposals' },
  },
  {
    title: 'Watch it arrive',
    body: 'Press Run again and follow the line in the ledger until it says it has arrived.',
    go: { href: '/ledger', label: 'Ledger' },
  },
  {
    title: 'Add a crisis',
    body: <>From a terminal, run <code className={s.cmd}>python ops/demo/drill.py crisis</code>. The disruptions appear on the timeline here. The project README covers the outage drill.</>,
  },
  {
    title: 'Reset for the next run',
    body: 'Press “Reset…”, then “Reset to tick 0”. Autopilot keeps its setting, so check it.',
    go: { href: '#sim-control', label: 'Controls' },
  },
];

/** A short walkthrough. Text and links only; nothing here changes the world. */
export default function DemoScript() {
  return (
    <Panel title="Demo script" meta="about 5 minutes">
      <ol className={s.script}>
        {STEPS.map((st, i) => (
          <li key={st.title} className={s.step}>
            <span className={s.stepNo} aria-hidden="true">{i + 1}</span>
            <div className={s.stepBody}>
              <h3>{st.title}</h3>
              <p>{st.body}</p>
              {st.go &&
                (st.go.href.startsWith('#') ? (
                  <a className={s.go} href={st.go.href}>{st.go.label}</a>
                ) : (
                  <Link className={s.go} href={st.go.href}>{st.go.label}</Link>
                ))}
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
