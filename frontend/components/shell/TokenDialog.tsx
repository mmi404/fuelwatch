'use client';

import { useEffect, useId, useState } from 'react';
import { useOperator } from '@/lib/operator';
import { ModalDialog } from './ModalDialog';
import s from './Shell.module.css';

/**
 * Operator token entry. Opens when `useOperator().askOpen` is true. If it was opened by a rejected write (not by the
 * operator pressing the top-bar button) while a token is already held, it says so inline.
 * The saved token is never shown back.
 */
export function TokenDialog({ manualOpen }: { manualOpen: React.MutableRefObject<boolean> }) {
  const { token, setToken, askOpen, ask } = useOperator();
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const errId = useId();
  const hintId = useId();

  useEffect(() => {
    if (!askOpen) return;
    setDraft('');
    setError(!manualOpen.current && token !== '' ? 'Token rejected. Enter the operator token again.' : null);
    manualOpen.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the moment of opening matters, not later token edits
  }, [askOpen]);

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim();
    if (!t) {
      setError('Enter the token to save it.');
      return;
    }
    setToken(t);
    ask(false);
  };

  return (
    <ModalDialog open={askOpen} onClose={() => ask(false)} labelledBy={titleId} className={s.narrow}>
      <form onSubmit={save} noValidate>
        <header className={s.modalHead}>
          <h2 id={titleId}>Operator token</h2>
          <span className={s.modalMeta}>{token ? 'Token set' : 'No token'}</span>
        </header>
        <div className={s.modalBody}>
          <p id={hintId} className={s.plain}>The token is stored in this browser only and is sent with actions that change the simulator.</p>
          <label className={s.fieldLabel} htmlFor={`${titleId}-field`}>Token</label>
          <input
            id={`${titleId}-field`}
            className={`field ${s.tokenField}`}
            type="password"
            name="operator-token"
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="off"
            data-autofocus
            data-1p-ignore
            placeholder={token ? 'Token set. Type a new one to replace it' : 'Paste the operator token'}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (error) setError(null);
            }}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${errId} ${hintId}` : hintId}
          />
          {error && (
            <p id={errId} className={s.fieldError} role="alert">
              {error}
            </p>
          )}
        </div>
        <footer className={s.modalFoot}>
          <button
            type="button"
            className="btn danger"
            disabled={!token}
            onClick={() => {
              setToken('');
              ask(false);
            }}
          >
            Clear
          </button>
          <span className={s.footSpacer} />
          <button type="button" className="btn" onClick={() => ask(false)}>
            Cancel
          </button>
          <button type="submit" className="btn primary">
            Save
          </button>
        </footer>
      </form>
    </ModalDialog>
  );
}
