'use client';

import { useEffect, useState } from 'react';
import { LevelChip, type LevelLike } from '@/components/ui/Level';
import { Panel } from '@/components/ui/Panel';
import { age, clockUtc, int, pct } from '@/lib/format';
import s from './SystemHealth.module.css';

type State = 'HEALTHY' | 'DEGRADED' | 'DOWN' | 'UNKNOWN';

/** GET /api/health/components (backend/fuelwatch/status_board.py). */
interface Report {
  overall: State;
  checked_at: string;
  components: { id: string; name: string; state: State; detail: string }[];
  http: { p95_latency_ms: number | null; error_rate: number | null; requests: number; window: 'recent' | 'since_start'; window_seconds: number };
  process: { resident_memory_bytes: number | null; cpu_seconds_total: number; cpu_percent: number | null };
}

const EVERY_MS = 4000;
const TIMEOUT_MS = 5000;

// The console's status shapes and colours, each always printed with its own word.
const LOOK: Record<State, { level: LevelLike; word: string }> = {
  HEALTHY: { level: 'OK', word: 'Healthy' },
  DEGRADED: { level: 'WARNING', word: 'Degraded' },
  DOWN: { level: 'CRITICAL', word: 'Down' },
  UNKNOWN: { level: 'UNKNOWN', word: 'Unknown' },
};

function StateChip({ state, solid }: { state: State; solid?: boolean }) {
  return <LevelChip level={LOOK[state].level} label={LOOK[state].word} solid={solid} />;
}

type View = { kind: 'loading' } | { kind: 'ok'; report: Report } | { kind: 'failed'; busy: boolean };

/** Polls the board. A failed or unanswered check drops the last report, so an old "Healthy" is never left on screen. */
function useReport(): View {
  const [view, setView] = useState<View>({ kind: 'loading' });
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    const check = async () => {
      const one = new AbortController();
      const cancel = () => one.abort();
      abort.signal.addEventListener('abort', cancel);
      const limit = setTimeout(cancel, TIMEOUT_MS);
      let next: View;
      try {
        const res = await fetch('/api/health/components', { cache: 'no-store', signal: one.signal });
        // 503 with Retry-After is the backend refusing optional reads under load: it was reached.
        next = res.ok ? { kind: 'ok', report: (await res.json()) as Report } : { kind: 'failed', busy: res.status === 503 && res.headers.has('retry-after') };
      } catch {
        next = { kind: 'failed', busy: false };
      }
      clearTimeout(limit);
      abort.signal.removeEventListener('abort', cancel);
      if (stopped) return;
      setView(next);
      timer = setTimeout(check, EVERY_MS);
    };
    void check();
    return () => {
      stopped = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, []);
  return view;
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mono">{value}</dd>
    </div>
  );
}

function Board({ report }: { report: Report }) {
  const { http, process: proc } = report;
  const span = age(http.window_seconds);
  return (
    <>
      <ul className={s.list}>
        {report.components.map((c) => (
          <li key={c.id} className={s.row}>
            <span className={s.name}>{c.name}</span>
            <span className={s.state}>
              <StateChip state={c.state} />
            </span>
            <span className={s.detail}>{c.detail}</span>
          </li>
        ))}
      </ul>
      <dl className={s.figures}>
        <Figure label="p95 latency" value={http.p95_latency_ms == null ? 'No requests' : `${http.p95_latency_ms < 10 ? http.p95_latency_ms.toFixed(1) : int(http.p95_latency_ms)} ms`} />
        <Figure label="Error rate (5xx)" value={http.error_rate == null ? 'No requests' : pct(http.error_rate)} />
        <Figure label="CPU, one core" value={proc.cpu_percent == null ? 'Not available' : `${proc.cpu_percent.toFixed(1)}%`} />
        <Figure label="Memory" value={proc.resident_memory_bytes == null ? 'Not available' : `${int(proc.resident_memory_bytes / 1048576)} MiB`} />
      </dl>
      <p className={s.note}>
        Latency and errors are for {int(http.requests)} requests to this backend{' '}
        {http.window === 'since_start' ? `since it started (${span})` : `in a recent window of ${span}`}, not counting this health check, metrics scrapes or the live stream. p95 is estimated from histogram buckets. Checked{' '}
        {clockUtc(report.checked_at)} UTC.
      </p>
    </>
  );
}

export default function SystemHealth() {
  const view = useReport();
  return (
    <Panel title="System health" actions={view.kind === 'ok' ? <span className={s.overall}><StateChip state={view.report.overall} solid /></span> : undefined}>
      {view.kind === 'loading' && <p className="muted">Checking the system.</p>}
      {view.kind === 'failed' && (
        <p role="alert" className={s.failed}>
          <StateChip state="UNKNOWN" />
          <span>
            {view.busy ? 'The backend is busy and refused the health check.' : 'The backend cannot be reached.'} The state of its components is unknown. Trying again every few seconds.
          </span>
        </p>
      )}
      {view.kind === 'ok' && <Board report={view.report} />}
    </Panel>
  );
}
