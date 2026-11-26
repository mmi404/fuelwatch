'use client';

import { useState } from 'react';
import { Panel } from '@/components/ui/Panel';
import { clockUtc, liters, pct, simClock } from '@/lib/format';
import { useLive } from '@/lib/world';
import { useOperator } from '@/lib/operator';
import StepButton from './StepButton';
import { describeError } from './util';
import s from './Scenario.module.css';

type Action = 'run' | 'pause' | 'step' | 'reset';
const NAME: Record<Action, string> = { run: 'Run', pause: 'Pause', step: 'Step', reset: 'Reset' };
const DONE: Record<Action, string> = {
  run: 'The simulator accepted it.',
  pause: 'The simulator accepted it.',
  step: 'The simulator moved on by one tick.',
  reset: 'The simulator is back at tick 0 and paused.',
};

interface Result { action: Action; ok: boolean; text: string; at: string }

function StatusGlyph({ running }: { running: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      {running ? <path d="M5 3.5 14.5 9 5 14.5Z" fill="currentColor" /> : <path d="M5 3.5h3v11H5zM10 3.5h3v11h-3z" fill="currentColor" />}
    </svg>
  );
}

/** Simulation control: what the simulator is doing now, then run, pause, step and a two-step reset. */
export default function SimControl() {
  const { snapshot, status, error, refresh } = useLive();
  const { write, token, ask } = useOperator();
  const [busy, setBusy] = useState<Action | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const world = snapshot?.world ?? null;
  const inst = world?.instance ?? null;
  const metrics = world?.metrics ?? null;
  const old = status?.mode === 'STALE';
  // With old or missing data the run state is not known, so no button is ruled out because of it.
  const known = inst != null && !old && !error;
  const running = inst?.status === 'RUNNING';
  const autopilot = status?.autopilot.enabled ?? null;

  const allowed = () => {
    if (token) return true;
    ask(true);
    return false;
  };

  async function fire(action: Action) {
    setBusy(action);
    setResult(null);
    try {
      await write<unknown>('POST', `/sim/${action}`);
      setResult({ action, ok: true, text: DONE[action], at: clockUtc(new Date().toISOString()) });
    } catch (e) {
      setResult({ action, ok: false, text: describeError(e), at: clockUtc(new Date().toISOString()) });
    } finally {
      setBusy(null);
      refresh();
    }
  }

  /** A button the current run state makes pointless says why instead of doing nothing silently. */
  const plain = (action: Action, label: string, blocked: string | null) => (
    <button
      type="button"
      className="btn"
      aria-disabled={busy != null || blocked != null || undefined}
      title={blocked ?? undefined}
      onClick={() => {
        if (busy != null || blocked != null || !allowed()) return;
        void fire(action);
      }}
    >
      {label}
    </button>
  );

  return (
    <Panel title="Simulation control" meta={inst ? `${inst.scenario_id} v${inst.scenario_version} · seed ${inst.seed}` : 'waiting for data'}>
      <div id="sim-control" className={s.anchor}>
        {error && (
          <p className={s.errLine} role="note">
            {world ? 'The latest refresh failed, so this is the last data received. ' : 'No data from the console backend yet. '}
            Retrying every 5 s. <span className="mono">{error}</span>
          </p>
        )}
        <div className={`${s.readout} dimmable`}>
          <div className={s.stamp} data-state={inst ? (running ? 'running' : 'paused') : 'unknown'}>
            {inst && <StatusGlyph running={running} />}
            <span className={s.stampWord}>{inst ? (running ? 'RUNNING' : 'PAUSED') : 'NO DATA'}</span>
          </div>
          <dl className={s.figures}>
            <div><dt className="label">Tick</dt><dd className="mono">{inst ? inst.tick : '--'}</dd></div>
            <div><dt className="label">Sim clock</dt><dd className="mono">{simClock(inst?.sim_time)}</dd></div>
            <div><dt className="label">Service level</dt><dd className="mono">{pct(metrics?.service_level)}</dd></div>
            <div><dt className="label">Unmet demand</dt><dd className="mono">{liters(metrics?.unmet_demand_liters)}</dd></div>
          </dl>
        </div>
        {old && <p className={s.hint}>The data is old, so the run state shown here may be wrong.</p>}

        <div className={s.btnRow} role="group" aria-label="Simulation controls">
          {plain('run', 'Run', known && running ? 'The simulator is already running' : null)}
          {plain('pause', 'Pause', known && !running ? 'The simulator is already paused' : null)}
          {plain('step', 'Step 1 tick', known && running ? 'Pause first to move one tick at a time' : null)}
          <span className={s.spacer} />
          <StepButton
            label="Reset…"
            className="btn danger"
            needsConfirm
            confirmLabel="Reset to tick 0"
            busy={busy != null}
            allowed={allowed}
            onGo={() => void fire('reset')}
            warning={
              <>
                This restarts the simulated world at tick 0, paused. Injected disruptions are removed, open proposals expire and the ledger starts
                empty. The audit trail is kept. It cannot be undone.
                {autopilot && <strong> Autopilot is ON and stays on after the reset.</strong>}
              </>
            }
          />
        </div>
        <p className={s.hint}>One tick is {inst?.tick_minutes ?? 15} minutes of simulated time.</p>

        {!token && (
          <p className={s.hint}>
            These controls need the operator token. <button type="button" className={s.linkBtn} onClick={() => ask(true)}>Enter the token</button>
          </p>
        )}

        <div className={s.resultSlot} role="status">
          {busy && <p className={s.hint}>Sending {NAME[busy].toLowerCase()}…</p>}
          {!busy && result && (
            <p className={result.ok ? s.okLine : s.errLine}>
              <span className="mono">{result.at} UTC</span> <strong>{NAME[result.action]}{result.ok ? '' : ' failed'}.</strong> {result.text}
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}
