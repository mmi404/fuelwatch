'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { fuelName, pretty } from '@/lib/format';
import { riskKey, rankRisks } from '@/lib/risk';
import { useLive } from '@/lib/world';
import { LevelGlyph, levelLabel } from '@/components/ui/Level';
import { ModalDialog } from './ModalDialog';
import { Keys } from './ShortcutsDialog';
import { PAGES, keysOf } from './shortcuts';
import s from './Shell.module.css';

export interface PaletteActions {
  toggleTheme: () => void;
  toggleDensity: () => void;
  openToken: () => void;
  openHelp: () => void;
  /** Select a pair and bring its row into view. */
  selectPair: (key: string) => void;
}

interface Cmd {
  id: string;
  group: 'Go to' | 'Actions' | 'Pairs';
  label: string;
  hay: string;
  keys?: string[];
  seq?: boolean;
  meta?: React.ReactNode;
  run: () => void;
}

/** Lower is better; null means no match. Every word must match: as a substring (best) or as a subsequence. */
function score(hay: string, query: string): number | null {
  const h = hay.toLowerCase();
  let total = 0;
  for (const t of query.toLowerCase().split(/\s+/).filter(Boolean)) {
    const i = h.indexOf(t);
    if (i >= 0) {
      total += i === 0 || h[i - 1] === ' ' ? 0 : 4;
      continue;
    }
    let at = -1;
    let gaps = 0;
    for (const ch of t) {
      const n = h.indexOf(ch, at + 1);
      if (n < 0) return null;
      if (at >= 0) gaps += n - at - 1;
      at = n;
    }
    total += 20 + gaps;
  }
  return total;
}

export function CommandPalette({ open, onClose, actions }: { open: boolean; onClose: () => void; actions: PaletteActions }) {
  const titleId = useId();
  return (
    <ModalDialog open={open} onClose={onClose} labelledBy={titleId} className={s.palette}>
      <Body titleId={titleId} onClose={onClose} actions={actions} />
    </ModalDialog>
  );
}

function Body({ titleId, onClose, actions }: { titleId: string; onClose: () => void; actions: PaletteActions }) {
  const router = useRouter();
  const { risks, selected, refresh } = useLive();
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();

  const all = useMemo<Cmd[]>(() => {
    const pages: Cmd[] = PAGES.map((p) => ({
      id: p.id,
      group: 'Go to',
      label: `Go to ${p.label}`,
      hay: `go to ${p.label} page`,
      keys: keysOf(p.id),
      seq: true,
      run: () => router.push(p.href),
    }));
    const acts: Cmd[] = [
      { id: 'a-theme', group: 'Actions', label: 'Toggle theme (light or dark)', hay: 'toggle theme light dark', keys: keysOf('theme'), run: actions.toggleTheme },
      { id: 'a-density', group: 'Actions', label: 'Toggle density (comfortable or compact)', hay: 'toggle density compact comfortable rows', keys: keysOf('density'), run: actions.toggleDensity },
      { id: 'a-token', group: 'Actions', label: 'Enter operator token', hay: 'enter operator token key sign in', run: actions.openToken },
      { id: 'a-refresh', group: 'Actions', label: 'Refresh now', hay: 'refresh now reload data', run: () => refreshRef.current() },
      { id: 'a-help', group: 'Actions', label: 'Show shortcuts', hay: 'show shortcuts keyboard help cheat sheet', keys: keysOf('help'), run: actions.openHelp },
    ];
    const pairs: Cmd[] = rankRisks(risks).map((r) => {
      const key = riskKey(r);
      const name = `${pretty(r.station_id)} ${fuelName(r.fuel_type)}`;
      return {
        id: `p-${key}`,
        group: 'Pairs',
        label: name,
        hay: `select pair ${name} ${r.level}`,
        meta: (
          <span className={s.pairLevel}>
            <LevelGlyph level={r.level} size={12} />
            {levelLabel(r.level)}
          </span>
        ),
        run: () => actions.selectPair(key),
      };
    });
    return [...pages, ...acts, ...pairs];
    // eslint-disable-next-line react-hooks/exhaustive-deps -- actions are stable callbacks from the shell
  }, [risks, router]);

  const rows = useMemo(() => {
    if (!q.trim()) return all;
    return all
      .map((c, i) => ({ c, i, sc: score(c.hay, q) }))
      .filter((x): x is { c: Cmd; i: number; sc: number } => x.sc !== null)
      .sort((a, b) => a.sc - b.sc || a.i - b.i)
      .map((x) => x.c);
  }, [all, q]);

  const idx = Math.min(active, Math.max(0, rows.length - 1));
  const optId = (i: number) => `${listId}-o${i}`;

  useEffect(() => {
    document.getElementById(optId(idx))?.scrollIntoView({ block: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, rows]);

  const run = (c: Cmd) => {
    onClose();
    setTimeout(c.run, 0);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(rows.length ? (idx + 1) % rows.length : 0);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(rows.length ? (idx - 1 + rows.length) % rows.length : 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (rows[idx]) run(rows[idx]);
    } else if (e.key === 'Home' && e.ctrlKey) {
      setActive(0);
    }
  };

  return (
    <>
      <h2 id={titleId} className="sr-only">Command palette</h2>
      <div className={s.palInputRow}>
        <input
          className={s.palInput}
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={rows.length ? optId(idx) : undefined}
          aria-label="Type a command, page or pair such as mirpur diesel"
          placeholder="Command or pair, e.g. mirpur diesel"
          autoComplete="off"
          spellCheck={false}
          data-autofocus
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
        />
        <kbd className="kbd">Esc</kbd>
      </div>
      {rows.length === 0 ? (
        <p className={s.empty}>No command matches &ldquo;{q}&rdquo;. Try a station and fuel, such as &ldquo;tongi petrol&rdquo;.</p>
      ) : (
        <ul id={listId} role="listbox" aria-label="Commands" className={s.palList}>
          {rows.map((c, i) => {
            const heading = !q.trim() && (i === 0 || rows[i - 1].group !== c.group);
            return (
              <li key={c.id} role="presentation">
                {heading && <div className={`label ${s.palGroup}`}>{c.group === 'Pairs' ? 'Select a pair (ranked by risk)' : c.group}</div>}
                <div
                  id={optId(i)}
                  role="option"
                  aria-selected={i === idx}
                  data-active={i === idx || undefined}
                  className={s.palRow}
                  onMouseMove={() => i !== idx && setActive(i)}
                  onClick={() => run(c)}
                >
                  <span className={s.palLabel}>
                    {c.label}
                    {c.group === 'Pairs' && c.id === `p-${selected}` && <span className={`${s.palNote} muted`}> (selected)</span>}
                  </span>
                  {c.meta}
                  {c.keys && <Keys keys={c.keys} seq={c.seq} />}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <footer className={s.palFoot}>
        <span><kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> move</span>
        <span><kbd className="kbd">Enter</kbd> run</span>
        <span><kbd className="kbd">Esc</kbd> close</span>
      </footer>
    </>
  );
}
