'use client';

import { Fragment, useMemo, useState } from 'react';
import { Panel } from '@/components/ui/Panel';
import { age } from '@/lib/format';
import { ACTORS, actorWord, auditParts, describeAudit } from './model';
import type { AuditState } from './useLedger';
import s from './Ledger.module.css';

/** Anyone but the autopilot and the system is a person: "operator" with the shared token, else their own name. */
const actorGlyph = (actor: string) => (actor === 'autopilot' ? '◆' : actor === 'system' ? '○' : '●');

/** Typewriter report: one mono line per recorded action, newest first. Every string comes from the API as text. */
export default function AuditTrail({ state, refresh }: { state: AuditState; refresh: () => void }) {
  const [actor, setActor] = useState('');
  const [hideSim, setHideSim] = useState(false);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of state.rows) c[r.actor] = (c[r.actor] ?? 0) + 1;
    return c;
  }, [state.rows]);
  const actors = useMemo(() => [...ACTORS, ...Object.keys(counts).filter((a) => !(ACTORS as readonly string[]).includes(a))], [counts]);
  const simRows = state.rows.filter((r) => r.action.startsWith('sim.')).length;
  const shown = state.rows.filter((r) => (!actor || r.actor === actor) && !(hideSim && r.action.startsWith('sim.')));
  const total = state.rows.length;
  const filtering = actor !== '' || hideSim;

  let body: React.ReactNode;
  if (!state.loaded && state.error) {
    body = (
      <div className={s.empty} role="alert">
        <p className={s.emptyTitle}>The audit trail could not be read.</p>
        <p className="muted">{state.error}. The record is kept by the backend. Try again in a moment.</p>
        <button type="button" className="btn sm" onClick={refresh}>Try again</button>
      </div>
    );
  } else if (!state.loaded) {
    body = (
      <ol className={s.log} aria-busy="true" aria-label="Loading audit trail">
        {Array.from({ length: 7 }, (_, i) => (
          <li key={i} className={s.logRow}>
            <span className={`${s.skel} pulse`} style={{ width: 36, animationDelay: `${i * 70}ms` }} />
            <span className={`${s.skel} pulse`} style={{ width: 64 }} />
            <span className={`${s.skel} pulse`} style={{ width: 76 }} />
            <span className={`${s.skel} pulse`} style={{ width: `${40 + ((i * 17) % 35)}%`, minWidth: 140 }} />
          </li>
        ))}
      </ol>
    );
  } else if (total === 0) {
    body = (
      <div className={s.empty}>
        <p className={s.emptyTitle}>Nothing has been recorded yet.</p>
        <p className="muted">Approvals, rejections, autopilot changes, held or skipped lines and simulator controls are written here the moment they happen, with who did them.</p>
      </div>
    );
  } else if (shown.length === 0) {
    body = (
      <div className={s.empty}>
        <p className={s.emptyTitle}>No entries match.</p>
        <p className="muted">
          None of the {total} loaded entries {actor ? `are from ${actor}` : 'are left after hiding simulator controls'}.{state.hasMore ? ' Load older entries to look further back.' : ''}
        </p>
        <button type="button" className="btn sm" onClick={() => { setActor(''); setHideSim(false); }}>Clear filters</button>
      </div>
    );
  } else {
    let lastDay = '';
    body = (
      <ol className={`${s.log} dimmable`} aria-label="Audit trail, newest first">
        {shown.map((r) => {
          const { day, time } = auditParts(r.at);
          const divider = day !== lastDay;
          lastDay = day;
          return (
            <Fragment key={r.id}>
              {divider && <li className={s.logDay} aria-hidden="true"><span>{day} UTC</span></li>}
              <li className={`${s.logRow} ${state.fresh.has(r.id) ? `flash ${s.fresh}` : ''}`} data-actor={r.actor}>
                <span className={`${s.logId} faint`}>#{r.id}</span>
                <time className={s.logTime} dateTime={r.at} title={r.at}>{time}</time>
                <span className={s.logActor} data-actor={r.actor}><span aria-hidden="true">{actorGlyph(r.actor)}</span> {actorWord(r.actor)}</span>
                <span className={s.logText}>{describeAudit(r)}</span>
                <span className={`${s.logEntity} faint`} title={`${r.action} on ${r.entity} ${r.entity_id}`}>{r.action}</span>
              </li>
            </Fragment>
          );
        })}
      </ol>
    );
  }

  return (
    <Panel
      title="Audit trail"
      meta={!state.loaded ? 'loading' : filtering ? `${shown.length} of ${total} entries` : `${total} ${total === 1 ? 'entry' : 'entries'}${state.hasMore ? ', older available' : ''}`}
      actions={<button type="button" className="btn sm ghost" onClick={refresh} aria-label="Refresh audit trail">Refresh</button>}
      flush
    >
      {state.loaded && total > 0 && (
        <div className={s.toolbar}>
          <div className={s.chips} role="group" aria-label="Filter by actor">
            <button type="button" className={`${s.fchip} ${actor === '' ? s.fchipOn : ''}`} aria-pressed={actor === ''} onClick={() => setActor('')}>All <span className="mono">{total}</span></button>
            {actors.map((a) => (
              <button key={a} type="button" className={`${s.fchip} ${actor === a ? s.fchipOn : ''}`} aria-pressed={actor === a} disabled={!counts[a] && actor !== a} onClick={() => setActor(actor === a ? '' : a)}>
                <span aria-hidden="true">{actorGlyph(a)}</span> {actorWord(a)} <span className="mono">{counts[a] ?? 0}</span>
              </button>
            ))}
          </div>
          <label className={s.check}>
            <input type="checkbox" checked={hideSim} onChange={(e) => setHideSim(e.target.checked)} />
            Hide simulator controls <span className="mono faint">{simRows}</span>
          </label>
        </div>
      )}
      {state.error && state.loaded && (
        <p className={s.err} role="status">
          Could not refresh the audit trail ({state.error}). Showing the last good read{state.okAt ? `, ${age((Date.now() - state.okAt) / 1000)} old` : ''}.
          <button type="button" className="btn sm" onClick={refresh}>Retry</button>
        </p>
      )}
      {body}
      {state.loaded && total > 0 && (state.hasMore || state.loadingMore) && (
        <div className={s.more}>
          <button type="button" className="btn" onClick={state.loadMore} disabled={state.loadingMore}>{state.loadingMore ? 'Loading older entries' : 'Load older entries'}</button>
          <span className="faint">{total} loaded</span>
        </div>
      )}
    </Panel>
  );
}
