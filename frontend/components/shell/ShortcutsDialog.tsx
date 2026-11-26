'use client';

import { useId, useMemo, useState } from 'react';
import { ModalDialog } from './ModalDialog';
import { SHORTCUTS } from './shortcuts';
import s from './Shell.module.css';

export function Keys({ keys, seq = false }: { keys: string[]; seq?: boolean }) {
  return (
    <span className={s.keys}>
      {keys.map((k, i) => (
        <span key={`${k}-${i}`} className={s.keyWrap}>
          {i > 0 && seq && <span className={s.then}>then</span>}
          <kbd className="kbd">{k}</kbd>
        </span>
      ))}
    </span>
  );
}

/** Searchable cheat sheet, opened with ?. */
export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const titleId = useId();
  return (
    <ModalDialog open={open} onClose={onClose} labelledBy={titleId}>
      <Sheet titleId={titleId} onClose={onClose} />
    </ModalDialog>
  );
}

function Sheet({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  const [q, setQ] = useState('');
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return SHORTCUTS;
    return SHORTCUTS.filter((r) => `${r.label} ${r.keys.join(' ')} ${r.group}`.toLowerCase().includes(needle));
  }, [q]);
  const groups = Array.from(new Set(rows.map((r) => r.group)));
  return (
    <>
      <header className={s.modalHead}>
        <h2 id={titleId}>Keyboard shortcuts</h2>
        <span className={s.modalMeta}>Single keys work when no field has focus</span>
      </header>
      <div className={s.modalBody}>
        <label className="sr-only" htmlFor={`${titleId}-q`}>Search shortcuts</label>
        <input
          id={`${titleId}-q`}
          className={`field ${s.tokenField}`}
          type="search"
          placeholder="Search shortcuts"
          autoComplete="off"
          data-autofocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {rows.length === 0 ? (
          <p className={s.empty}>No shortcut matches &ldquo;{q}&rdquo;.</p>
        ) : (
          <div className={s.sheet}>
            {groups.map((g) => (
              <section key={g} aria-label={g}>
                <h3 className={`label ${s.sheetGroup}`}>{g}</h3>
                <ul>
                  {rows.filter((r) => r.group === g).map((r) => (
                    <li key={r.id} className={s.sheetRow}>
                      <span>{r.label}</span>
                      <Keys keys={r.keys} seq={r.seq} />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
      <footer className={s.modalFoot}>
        <span className={s.footSpacer} />
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </footer>
    </>
  );
}
