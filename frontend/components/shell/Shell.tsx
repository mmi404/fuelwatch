'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { simClock } from '@/lib/format';
import { useOperator } from '@/lib/operator';
import { rankRisks, riskKey } from '@/lib/risk';
import { useLive } from '@/lib/world';
import { CommandPalette } from './CommandPalette';
import { deriveHealth, useNow, useTickProgress } from './health';
import { IconDensity, IconKey, IconSearch, IconTheme, IconWarnTri, Mark } from './icons';
import { LiveRegion } from './LiveRegion';
import { usePrefs } from './prefs';
import { PAGES } from './shortcuts';
import { ShortcutsDialog } from './ShortcutsDialog';
import { StatusChip } from './StatusChip';
import { TokenDialog } from './TokenDialog';
import s from './Shell.module.css';

const NAV = PAGES.map((p) => ({ href: p.href, label: p.label }));
const GO: Record<string, string> = Object.fromEntries(PAGES.map((p) => [p.key, p.href]));

/** Application shell: title-block top bar, banner, content, quiet footer line, dialogs and the live region. */
export function Shell({ children }: { children: React.ReactNode }) {
  const live = useLive();
  const { token, me, ask } = useOperator();
  const pathname = usePathname();
  const router = useRouter();
  const now = useNow();
  const health = deriveHealth(live, now);
  const tick = useTickProgress(live.status?.tick ?? null, now);
  const { theme, density, toggleTheme, toggleDensity } = usePrefs();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const manualToken = useRef(false);

  const openToken = useCallback(() => {
    manualToken.current = true;
    ask(true);
  }, [ask]);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openHelp = useCallback(() => setHelpOpen(true), []);

  const selectPair = useCallback(
    (key: string) => {
      live.select(key);
      requestAnimationFrame(() => document.querySelector(`[data-pair="${key}"]`)?.scrollIntoView({ block: 'nearest' }));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `select` is a state setter
    [live.select],
  );

  // Global keys read the latest state through a ref so the listener is attached once.
  const latest = useRef({ live, pathname, router, toggleTheme, toggleDensity, selectPair });
  latest.current = { live, pathname, router, toggleTheme, toggleDensity, selectPair };
  const gAt = useRef(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const L = latest.current;
      const dialogOpen = !!document.querySelector('dialog[open]');
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => (o ? false : dialogOpen ? o : true));
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || e.isComposing || dialogOpen) return;
      const t = e.target instanceof Element ? e.target : null;
      if (t?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (e.shiftKey && k !== '?') return;

      if (gAt.current && Date.now() - gAt.current < 1500) {
        gAt.current = 0;
        const to = GO[k];
        if (to) {
          e.preventDefault();
          L.router.push(to);
          return;
        }
      }
      switch (k) {
        case 'g':
          gAt.current = Date.now();
          return;
        case '?':
          e.preventDefault();
          setHelpOpen(true);
          return;
        case '/':
          e.preventDefault();
          setPaletteOpen(true);
          return;
        case 't':
          if (!e.repeat) L.toggleTheme();
          return;
        case 'd':
          if (!e.repeat) L.toggleDensity();
          return;
        case 'j':
        case 'k': {
          const ranked = rankRisks(L.live.risks);
          if (!ranked.length) return;
          const i = ranked.findIndex((r) => riskKey(r) === L.live.selected);
          const next = i < 0 ? 0 : Math.min(ranked.length - 1, Math.max(0, i + (k === 'j' ? 1 : -1)));
          e.preventDefault();
          L.selectPair(riskKey(ranked[next]));
          return;
        }
        case 'Escape':
          if (L.live.selected) {
            e.preventDefault();
            L.live.select(null);
          }
          return;
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const clock = live.snapshot ? simClock(live.snapshot.world.instance.sim_time) : '--';
  const tickNo = live.status?.tick ?? live.snapshot?.world.instance.tick;
  const hasToken = token !== '';
  // With the shared token the server answers "operator", which is no one in particular: keep saying "Token set".
  const named = me && me.name !== 'operator' ? me : null;
  const showBanner = !!live.error && health.hasData;
  const active = (href: string) => (href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`));

  return (
    <>
      <a className={s.skip} href="#main">Skip to content</a>
      <header className={s.topbar}>
        <div className={s.brand}>
          <Mark />
          <div className={s.brandText}>
            <span className={s.brandName}>FuelWatch</span>
            <span className={s.brandSub}>Dispatch board</span>
          </div>
        </div>

        <nav className={s.nav} aria-label="Primary">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={s.navLink} aria-current={active(n.href) ? 'page' : undefined}>
              {n.label}
            </Link>
          ))}
        </nav>

        <div className={s.spacer} />

        <div className={`${s.cell} ${s.clockCell} ${s.hideMid}`}>
          <span className={s.cap}>Sim time</span>
          <span className={s.val}>
            {clock}
            <span className={s.tick}>tick {tickNo ?? '--'}</span>
          </span>
        </div>

        <StatusChip health={health} tickProgress={tick} />

        <button type="button" className={`${s.cell} ${s.btnCell} ${s.hideSmall}`} onClick={toggleTheme} aria-pressed={theme === 'dark'} title="Toggle theme (t)">
          <IconTheme />
          <span className={`${s.stack} ${s.collapse}`}>
            <span className={s.cap}>Theme</span>
            <span className={s.val}>{theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : '--'}</span>
          </span>
        </button>
        <button type="button" className={`${s.cell} ${s.btnCell} ${s.hideMid}`} onClick={toggleDensity} aria-pressed={density === 'compact'} title="Toggle density (d)">
          <IconDensity compact={density === 'compact'} />
          <span className={`${s.stack} ${s.collapse}`}>
            <span className={s.cap}>Density</span>
            <span className={s.val}>{density === 'compact' ? 'Compact' : 'Comfortable'}</span>
          </span>
        </button>
        <button type="button" className={`${s.cell} ${s.btnCell} ${s.hideSmall}`} onClick={openToken} title="Operator token: stored in this browser only">
          <IconKey set={hasToken} />
          <span className={s.stack}>
            <span className={s.cap}>{named ? named.role : 'Operator'}</span>
            <span className={s.val}>{named ? named.name : hasToken ? 'Token set' : 'No token'}</span>
          </span>
        </button>
        <button type="button" className={`${s.cell} ${s.btnCell} ${s.lastCell}`} onClick={openPalette} aria-label="Open command palette" title="Command palette (Ctrl or Cmd K)">
          <IconSearch />
          <span className={`${s.stack} ${s.collapse} ${s.hideNarrow}`} aria-hidden="true">
            <span className={s.cap}>Palette</span>
            <span className={s.val}>Ctrl K</span>
          </span>
        </button>
      </header>

      {showBanner && (
        <div className={s.banner}>
          <IconWarnTri />
          <span>
            Cannot reach the backend. Showing data from <span className="mono">{health.lastOk}</span>. Retrying.
          </span>
          <button type="button" className="btn sm" onClick={live.refresh}>Retry now</button>
        </div>
      )}

      <main id="main" tabIndex={-1} className={s.main}>
        <div data-stale={health.stale ? 'true' : 'false'}>{children}</div>
      </main>

      <footer className={s.hint}>
        Press <kbd className="kbd">?</kbd> for shortcuts
      </footer>

      <LiveRegion />
      <TokenDialog manualOpen={manualToken} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} actions={{ toggleTheme, toggleDensity, openToken, openHelp, selectPair }} />
      <ShortcutsDialog open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  );
}
