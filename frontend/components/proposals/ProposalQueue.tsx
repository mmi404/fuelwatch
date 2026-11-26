'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Autopilot, Proposal, Route } from '@/lib/api';
import { int, liters } from '@/lib/format';
import { useLive } from '@/lib/world';
import { useOperator } from '@/lib/operator';
import { levelRank } from '@/lib/risk';
import { Panel } from '@/components/ui/Panel';
import { Act, Ctx, HistoryRow, ProposalCard } from './ProposalCard';
import { Bucket, WriteError, confidenceWord, describeError, isOpen, readCard, stillMoving } from './model';
import s from './ProposalQueue.module.css';

/** What this operator did in this session, kept above the cards so it survives a card moving between sections. */
interface Mark {
  decided?: Act;
  error?: WriteError;
  /** The server's answer, used until the next refresh brings the same proposal back. */
  answer?: Proposal;
  pinned?: boolean;
}

/** Puts focus back on the card after a write, once the refreshed list has placed it (it may have moved sections). */
function focusCard(id: number, tries = 12) {
  const el = document.getElementById(`proposal-${id}`);
  if (el) el.focus({ preventScroll: false });
  else if (tries > 0) setTimeout(() => focusCard(id, tries - 1), 100);
}

/** The guards the autopilot applies to one proposal (backend executor), said in words. They hold even while it is off. */
function reviewReason(p: Proposal, total: number, ap: Autopilot | undefined): string | null {
  if (!ap || p.status !== 'PROPOSED') return null;
  if (p.confidence < ap.min_confidence)
    return `Confidence ${p.confidence.toFixed(2)} is below ${ap.min_confidence.toFixed(2)}, so the autopilot will not approve this.`;
  if (ap.enabled && total > ap.max_liters_per_proposal)
    return `${liters(total)} is over the autopilot's limit of ${liters(ap.max_liters_per_proposal)} per proposal.`;
  return null;
}

export default function ProposalQueue() {
  const { status, risks, proposals, snapshot, lastOkAt, error, refresh } = useLive();
  const { token, ask, write } = useOperator();
  const [marks, setMarks] = useState<Record<number, Mark>>({});
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState<Record<number, Act>>({});
  const inFlight = useRef<Set<number>>(new Set());
  /** Which waiting cards are open. A card with no entry is open only if it is first in the queue. */
  const [openCards, setOpenCards] = useState<Record<number, boolean>>({});

  const act = useCallback(
    async (p: Proposal, kind: Act) => {
      if (inFlight.current.has(p.id)) return;
      if (!token) {
        ask(true);
        return;
      }
      inFlight.current.add(p.id);
      setBusy((b) => ({ ...b, [p.id]: kind }));
      setMarks((m) => (m[p.id]?.error ? { ...m, [p.id]: { ...m[p.id], error: undefined } } : m));
      const hadFocus = document.getElementById(`proposal-${p.id}`)?.contains(document.activeElement) ?? false;
      const stale = status?.mode === 'DEGRADED' || status?.mode === 'STALE';
      const body = kind === 'approve' && stale && overrides[p.id] ? { override_stale: true } : undefined;
      try {
        const answer = await write<Proposal>('POST', `/proposals/${p.id}/${kind}`, body);
        setMarks((m) => ({ ...m, [p.id]: { decided: kind, answer, pinned: true } }));
        refresh();
        if (hadFocus) focusCard(p.id);
      } catch (e) {
        setMarks((m) => ({ ...m, [p.id]: { ...m[p.id], error: describeError(e, status?.tick ?? null) } }));
        refresh();
        if (hadFocus) document.getElementById(`proposal-${p.id}`)?.querySelector<HTMLElement>(`[data-act="${kind}"]`)?.focus();
      } finally {
        inFlight.current.delete(p.id);
        setBusy((b) => {
          const { [p.id]: _gone, ...rest } = b;
          return rest;
        });
      }
    },
    [token, ask, write, refresh, status, overrides],
  );

  const ctx: Ctx | null = useMemo(() => {
    if (!status) return null;
    const routes: Record<string, Route> = {};
    for (const r of snapshot?.world.routes ?? []) routes[r.id] = r;
    return {
      tick: status.tick,
      tickMinutes: status.tick_minutes,
      simTick: snapshot?.world.instance.tick ?? status.tick,
      simTime: snapshot?.world.instance.sim_time ?? null,
      mode: status.mode,
      epochId: status.epoch?.id ?? null,
      gateOpen: status.execution?.gate_open ?? true,
      hasToken: token !== '',
      routes,
    };
  }, [status, snapshot, token]);

  const groups = useMemo(() => {
    // Live list first; a proposal this operator just decided stays visible from the server's answer until the refresh has it.
    const byId = new Map<number, Proposal>(proposals.map((p) => [p.id, p]));
    for (const [id, m] of Object.entries(marks)) if (m.answer && !byId.has(Number(id))) byId.set(Number(id), m.answer);
    const all = [...byId.values()].map((p) => ({ p, v: readCard(p) }));

    const bucketOf = (p: Proposal): Bucket => {
      if (isOpen(p.status)) return 'awaiting';
      if (p.status === 'APPROVED' || p.status === 'EXECUTING') return 'active';
      if (marks[p.id]?.pinned) return 'active';
      if ((p.status === 'EXECUTED' || p.status === 'PARTIAL') && stillMoving(p)) return 'active';
      return 'history';
    };
    const riskOf = new Map(risks.map((r) => [`${r.station_id}:${r.fuel_type}`, r]));
    const urgency = (x: { p: Proposal; v: ReturnType<typeof readCard> }) => {
      const r = riskOf.get(`${x.p.station_id}:${x.p.fuel_type}`);
      const lvl = r?.level ?? x.v.level ?? 'UNKNOWN';
      return { rank: levelRank(lvl), hours: r?.hours_p90 ?? x.v.hoursP90 ?? 1e9, level: lvl };
    };
    const awaiting = all
      .filter((x) => bucketOf(x.p) === 'awaiting')
      .map((x) => ({ ...x, u: urgency(x) }))
      .sort((a, b) => b.u.rank - a.u.rank || a.u.hours - b.u.hours || b.p.id - a.p.id);
    const newest = (a: { p: Proposal }, b: { p: Proposal }) => b.p.id - a.p.id;
    const active = all.filter((x) => bucketOf(x.p) === 'active').map((x) => ({ ...x, u: urgency(x) })).sort(newest);
    const history = all.filter((x) => bucketOf(x.p) === 'history').sort(newest);
    return { awaiting, active, history };
  }, [proposals, risks, marks]);

  // The first card stays open when a more urgent one arrives above it, so its buttons do not vanish under the pointer.
  const firstId = groups.awaiting[0]?.p.id;
  useEffect(() => {
    if (firstId !== undefined) setOpenCards((o) => (o[firstId] === undefined ? { ...o, [firstId]: true } : o));
  }, [firstId]);

  const loading = lastOkAt == null && !error;
  const ap = status?.autopilot;
  const meta = loading ? 'loading' : `${groups.awaiting.length} awaiting decision`;
  const clearError = (id: number) => setMarks((m) => ({ ...m, [id]: { ...m[id], error: undefined } }));
  const dismiss = (id: number) =>
    setMarks((m) => {
      const { [id]: _gone, ...rest } = m;
      return rest;
    });

  const card = (x: (typeof groups.awaiting)[number], variant: 'open' | 'active') => {
    const m = marks[x.p.id];
    // a card that is sending, or that holds a refusal to read, is never folded
    const foldable = variant === 'open' && groups.awaiting.length > 1 && !busy[x.p.id] && !m?.error;
    const isShown = openCards[x.p.id] ?? x.p.id === firstId;
    return (
      <li key={x.p.id}>
        <ProposalCard
          p={x.p}
          view={x.v}
          ctx={ctx as Ctx}
          pairLevel={x.u.level}
          review={reviewReason(x.p, x.v.total, status?.autopilot)}
          variant={variant}
          busy={busy[x.p.id]}
          override={!!overrides[x.p.id]}
          error={m?.error}
          decided={m?.decided}
          onAct={(a) => void act(x.p, a)}
          onOverride={(v) => setOverrides((o) => ({ ...o, [x.p.id]: v }))}
          onClearError={() => clearError(x.p.id)}
          onDismiss={variant === 'active' && m && !['APPROVED', 'EXECUTING'].includes(x.p.status) ? () => dismiss(x.p.id) : undefined}
          onAskToken={() => ask(true)}
          collapsed={foldable && !isShown}
          onToggle={foldable ? (v) => setOpenCards((o) => ({ ...o, [x.p.id]: v })) : undefined}
        />
      </li>
    );
  };

  return (
    <div id="proposal-queue">
      <Panel title="Proposals" meta={meta} flush>
        {ap?.enabled && (
          <div className={s.strip} role="note">
            <strong>Autopilot is on:</strong> approves up to {liters(ap.max_liters_per_proposal)} per proposal and {liters(ap.max_liters_per_tick)} per tick at confidence{' '}
            {'>='} {ap.min_confidence.toFixed(2)} ({confidenceWord(ap.min_confidence)} or better), at most {int(ap.max_proposals_per_tick)} proposals per tick. Proposals inside these guards are approved
            automatically; anything outside waits here for you. <Link href="/scenario">Change guards</Link>
          </div>
        )}

        {loading || !ctx ? (
          error ? (
            <div className={s.errorState} role="alert">
              <strong>Proposals are not available.</strong>
              <span className="muted">The console could not read them: {error}. It retries every 5 seconds.</span>
              <button type="button" className="btn sm" onClick={refresh}>Retry now</button>
            </div>
          ) : (
            <div className={s.skel} aria-hidden="true">
              <span /><span /><span /><span /><span /><span /><span />
            </div>
          )
        ) : (
          <>
            {groups.awaiting.length === 0 && groups.active.length === 0 ? (
              <div className={s.empty}>
                <strong>Nothing awaiting a decision.</strong>
                <span className="muted">Stock cover is adequate or proposals have been dispatched.</span>
              </div>
            ) : null}

            {groups.awaiting.length > 0 && (
              <ul className={s.list} aria-label="Proposals awaiting a decision">
                {groups.awaiting.map((x) => card(x, 'open'))}
              </ul>
            )}

            {groups.active.length > 0 && (
              <>
                <div className={s.sectionHead}>
                  <h3 className="label">In progress ({groups.active.length})</h3>
                  <span className={s.sectionNote}>A sent shipment cannot be recalled.</span>
                </div>
                <ul className={s.list} aria-label="Proposals in progress">
                  {groups.active.map((x) => card(x, 'active'))}
                </ul>
              </>
            )}

            {groups.history.length > 0 && (
              <details>
                <summary className={s.histSummary}>
                  <h3>History ({groups.history.length})</h3>
                </summary>
                <ul className={s.list} aria-label="Closed proposals">
                  {groups.history.map((x) => (
                    <HistoryRow key={x.p.id} p={x.p} v={x.v} />
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
