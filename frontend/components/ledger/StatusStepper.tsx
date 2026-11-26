import { LevelChip, LevelGlyph } from '@/components/ui/Level';
import { STEPS, statusWord } from './model';
import s from './Ledger.module.css';

type Pip = 'done' | 'current' | 'todo';

/** One pip of the stepper. Shape carries the state (check, ring-dot, empty ring), never colour. */
function PipGlyph({ state, size = 14 }: { state: Pip; size?: number }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      {state === 'done' && (
        <>
          <circle cx="7" cy="7" r="5.6" fill="currentColor" fillOpacity="0.14" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4.5 7.2 6.3 9 9.6 5.2" {...common} />
        </>
      )}
      {state === 'current' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...common} strokeWidth="2" />
          <circle cx="7" cy="7" r="2" fill="currentColor" />
        </>
      )}
      {state === 'todo' && <circle cx="7" cy="7" r="5.6" {...common} strokeDasharray="1 2.4" />}
    </svg>
  );
}

/** Glyph for a status word in filter chips: same family as the stepper, plus the three bad endings. */
export function StatusGlyph({ status, size = 12 }: { status: string; size?: number }) {
  if (status === 'FAILED') return <LevelGlyph level="CRITICAL" size={size} />;
  if (status === 'REJECTED') return <LevelGlyph level="WARNING" size={size} />;
  if (status === 'UNKNOWN') return <LevelGlyph level="UNKNOWN" size={size} />;
  const state: Pip = status === 'ARRIVED' ? 'done' : 'current';
  return (
    <span style={{ color: 'var(--ink-2)', display: 'inline-flex' }}>
      <PipGlyph state={state} size={size} />
    </span>
  );
}

/**
 * SENDING, SUBMITTED, IN TRANSIT, ARRIVED as four pips on a hairline, with the current word stamped beside them.
 * FAILED, REJECTED and UNKNOWN are not steps: they are terminal stamps with their own glyph and word.
 * `full` writes all four words under the pips (expanded row and phone width).
 */
export default function StatusStepper({ status, full = false }: { status: string; full?: boolean }) {
  const bad = status === 'FAILED' || status === 'REJECTED' || status === 'UNKNOWN';
  if (bad) {
    const level = status === 'FAILED' ? 'CRITICAL' : status === 'REJECTED' ? 'WARNING' : 'UNKNOWN';
    return <LevelChip level={level} solid label={statusWord(status)} />;
  }
  const idx = (STEPS as readonly string[]).indexOf(status);
  const at = idx < 0 ? 0 : idx;
  const pipState = (i: number): Pip => (i < at || (status === 'ARRIVED' && i === at) ? 'done' : i === at ? 'current' : 'todo');
  const summary = `${statusWord(status)}, step ${at + 1} of ${STEPS.length}`;
  return (
    <span className={`${s.stepper} ${full ? s.stepperFull : ''}`} role="img" aria-label={summary} data-status={status}>
      <span className={s.pips} aria-hidden="true">
        {STEPS.map((step, i) => (
          <span key={step} className={`${s.pip} ${s[pipState(i)]}`} title={statusWord(step)}>
            <PipGlyph state={pipState(i)} />
            <span className={s.pipWord}>{statusWord(step)}</span>
          </span>
        ))}
      </span>
      <span className={s.stamp} aria-hidden="true">{statusWord(status)}</span>
    </span>
  );
}
