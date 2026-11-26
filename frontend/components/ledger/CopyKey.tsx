'use client';

import { useEffect, useRef, useState } from 'react';
import s from './Ledger.module.css';

/** Truncate in the middle: "fw-p12-l1…-l14". Short keys are shown whole. */
export const shortKey = (key: string, head = 10, tail = 4) => (key.length <= head + tail + 1 ? key : `${key.slice(0, head)}…${key.slice(-tail)}`);

/** The idempotency key in mono, truncated, with a copy button. Text only; the full key is in the title and the copy. */
export default function CopyKey({ value, label, buttonOnly = false }: { value: string; label: string; buttonOnly?: boolean }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(value);
      ok = true;
    } catch {
      ok = false;
    }
    setState(ok ? 'copied' : 'failed');
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), 1800);
  };

  return (
    <span className={s.key}>
      {!buttonOnly && <code className={`mono ${s.keyText}`} title={value}>{shortKey(value)}</code>}
      <button type="button" className={`btn sm ghost ${s.copy}`} onClick={copy} aria-label={`Copy idempotency key for ${label}`}>
        {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}
      </button>
      <span className="sr-only" role="status">{state === 'copied' ? 'Key copied' : state === 'failed' ? 'Copy failed' : ''}</span>
    </span>
  );
}
