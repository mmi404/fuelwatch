'use client';

import { useEffect, useId, useRef, useState } from 'react';
import s from './Scenario.module.css';

interface Props {
  label: string;
  className?: string;
  /** When true the button only opens a question; the action runs from the second button. */
  needsConfirm: boolean;
  confirmLabel: string;
  /** What the second press will do, in plain words. */
  warning: React.ReactNode;
  /** True while any write is in flight: presses are ignored. */
  busy: boolean;
  /** Runs before anything happens; false stops it (used to ask for the token first). */
  allowed: () => boolean;
  onGo: () => void;
}

/**
 * A button with an optional second, deliberate step. The question opens below the row rather than under the pointer, so a
 * double click cannot answer it, and focus goes to Cancel so a held Enter key cannot either. The buttons use
 * aria-disabled so that focus is not thrown away while a write is in flight.
 */
export default function StepButton({ label, className = 'btn', needsConfirm, confirmLabel, warning, busy, allowed, onGo }: Props) {
  const [asking, setAsking] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const warnId = useId();

  useEffect(() => {
    if (asking) cancel.current?.focus();
  }, [asking]);
  useEffect(() => {
    if (!needsConfirm) setAsking(false);
  }, [needsConfirm]);

  const close = () => {
    setAsking(false);
    trigger.current?.focus();
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={className}
        aria-disabled={busy || undefined}
        aria-expanded={needsConfirm ? asking : undefined}
        onClick={() => {
          if (busy || !allowed()) return;
          if (needsConfirm) setAsking(true);
          else onGo();
        }}
      >
        {label}
      </button>
      {asking && needsConfirm && (
        <div
          className={s.confirm}
          role="group"
          aria-label={confirmLabel}
          aria-describedby={warnId}
          onKeyDown={(e) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
            close();
          }}
        >
          <p id={warnId}>{warning}</p>
          <div className={s.confirmRow}>
            <button ref={cancel} type="button" className="btn" onClick={close}>
              Cancel
            </button>
            <button
              type="button"
              className="btn danger"
              aria-disabled={busy || undefined}
              onClick={() => {
                if (busy) return;
                close();
                onGo();
              }}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
