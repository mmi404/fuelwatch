'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { age } from '@/lib/format';
import { useLive } from '@/lib/world';
import { POLL_S, type Health, type Mode } from './health';
import { IconWarnTri } from './icons';
import s from './Shell.module.css';

const COLOR: Record<Mode, string> = { LIVE: 'var(--ok)', DEGRADED: 'var(--warn)', STALE: 'var(--ink-2)', STARTING: 'var(--ink-3)' };

function Shape({ mode }: { mode: Mode }) {
  if (mode === 'LIVE')
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
        <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.5" />
        <circle cx="7" cy="7" r="3.6" fill="currentColor" className="pulse" />
      </svg>
    );
  if (mode === 'DEGRADED') return <IconWarnTri />;
  if (mode === 'STALE')
    return (
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
        <rect x="1.8" y="1.8" width="10.4" height="10.4" rx="1" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2.6 2" />
      </svg>
    );
  return null;
}

/** Always-visible connection state: shape + word + an age that ticks every second. Press for details. */
export function StatusChip({ health, tickProgress }: { health: Health; tickProgress: { progress: number | null; idle: boolean } }) {
  const live = useLive();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const popId = useId();
  const { mode, ageSec, tone } = health;

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [open]);

  const ageText = ageSec == null ? null : age(ageSec);
  const color = COLOR[mode] === 'var(--ink-2)' && tone === 'crit' ? 'var(--crit)' : COLOR[mode];
  const st = live.status;
  const hasEpoch = st?.epoch != null;

  return (
    <div
      className={s.chipCell}
      ref={wrap}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.preventDefault();
          setOpen(false);
          btn.current?.focus();
        }
      }}
    >
      <button
        ref={btn}
        type="button"
        className={`${s.chip} ${mode === 'STALE' ? s.chipStale : ''}`}
        style={{ ['--c' as string]: color }}
        aria-expanded={open}
        aria-controls={popId}
        onClick={() => setOpen((o) => !o)}
      >
        {mode === 'STARTING' ? (
          <>
            <span className={`${s.skeleton} pulse`} aria-hidden="true" />
            <span className={s.chipWord}>Connecting</span>
            {live.error && <span className={s.chipDetail}>, retrying</span>}
          </>
        ) : (
          <>
            <Shape mode={mode} />
            <span className={s.chipWord}>{mode}</span>
            {mode === 'DEGRADED' && <span className={`${s.chipDetail} ${s.hideNarrow}`}>, polling</span>}
            {mode === 'STALE' && health.lastOk && <span className={`${s.chipDetail} ${s.hideNarrow}`}>, data as of {health.lastOk}</span>}
            {ageText && <span className={`${s.chipAge} ${tone === 'warn' ? s.toneWarn : tone === 'crit' ? s.toneCrit : ''}`}>{ageText}</span>}
          </>
        )}
        <span className="sr-only"> status details</span>
      </button>
      {tickProgress.progress != null && (
        <span className={s.tickTrack} aria-hidden="true">
          <span className={`${s.tickFill} ${tickProgress.idle ? s.tickIdle : ''}`} style={{ width: `${Math.round(tickProgress.progress * 100)}%` }} />
        </span>
      )}
      {open && (
        <div id={popId} className={s.pop} role="group" aria-label="Connection details">
          <dl className={s.popGrid}>
            <dt>Data age</dt>
            <dd>
              {ageText ?? 'no data yet'}
              {st?.age_seconds != null && <span className="faint"> (backend reported {age(st.age_seconds)})</span>}
            </dd>
            <dt>Updates</dt>
            <dd>{live.streaming ? 'Event stream connected' : `Event stream down, polling every ${POLL_S} s`}</dd>
            <dt>Breakers</dt>
            <dd>
              {st
                ? (['read', 'write'] as const).map((k) => (
                    <span key={k} className={s.popBreaker}>
                      {k} {st.breakers[k].state.toLowerCase()}
                      {st.breakers[k].opened_total > 0 && <span className="faint"> (opened {st.breakers[k].opened_total}x)</span>}
                    </span>
                  ))
                : 'unknown'}
            </dd>
            <dt>Retries</dt>
            <dd>{health.retries > 0 ? `about ${health.retries} since the last good data (${POLL_S} s poll)` : 'none pending'}</dd>
            <dt>Epoch</dt>
            <dd>{hasEpoch ? `#${st?.epoch?.id}` : 'none'}</dd>
            <dt>Tick</dt>
            <dd>{st ? `${st.tick} (${st.tick_minutes} min per tick)` : 'n/a'}</dd>
            <dt>Last error</dt>
            <dd className={live.error ? s.popErr : undefined}>{live.error ?? 'none'}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
