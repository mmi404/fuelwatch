'use client';

import { KeyboardEvent } from 'react';
import type { Proposal, Route, Status } from '@/lib/api';
import { hours, int, liters, plural, pretty, tickClock } from '@/lib/format';
import { LevelChip, LevelGlyph } from '@/components/ui/Level';
import { FuelTag } from '@/components/ui/Fuel';
import {
  CONFIDENCE_ADVICE,
  CardView,
  POLICY,
  STATE_WORD,
  WriteError,
  actionSentence,
  altTitle,
  cover,
  evidence,
  failureText,
  isOpen,
  lineState,
  planMadeAt,
} from './model';
import s from './ProposalQueue.module.css';

export type Act = 'approve' | 'reject';

/** What every card needs to know about "now". */
export interface Ctx {
  tick: number;
  tickMinutes: number;
  simTick: number;
  simTime: string | null;
  mode: Status['mode'];
  epochId: number | null;
  gateOpen: boolean;
  hasToken: boolean;
  routes: Record<string, Route>;
}

export interface CardProps {
  p: Proposal;
  view: CardView;
  ctx: Ctx;
  /** The pair's current level from /api/risks, else the level on the card. */
  pairLevel: Proposal['explanation']['headline']['level'] | null;
  variant: 'open' | 'active';
  /** Why the autopilot leaves this waiting proposal to a person, when it would. */
  review: string | null;
  busy: Act | undefined;
  override: boolean;
  error: WriteError | undefined;
  /** Set when this operator decided it in this session. */
  decided: Act | undefined;
  onAct: (a: Act) => void;
  onOverride: (v: boolean) => void;
  onClearError: () => void;
  onDismiss: (() => void) | undefined;
  onAskToken: () => void;
  /** Short form of a waiting card: the sentence and two facts, with no way to decide until it is opened. */
  collapsed: boolean;
  /** Undefined when there is nothing to fold: one waiting card, or a card that is not waiting. */
  onToggle: ((open: boolean) => void) | undefined;
}

const clockOf = (ctx: Ctx, tick: number) => tickClock(ctx.simTime, ctx.simTick, tick, ctx.tickMinutes);
const spanHours = (ctx: Ctx, ticks: number) => (ticks * ctx.tickMinutes) / 60;

function StateGlyph({ status }: { status: string }) {
  const c = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true" focusable="false" style={{ flex: 'none' }}>
      {status === 'PROPOSED' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} />
          <circle cx="7" cy="7" r="1.6" fill="currentColor" />
        </>
      )}
      {status === 'HELD' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} />
          <path d="M5.5 4.8v4.4M8.5 4.8v4.4" {...c} />
        </>
      )}
      {(status === 'APPROVED' || status === 'EXECUTING') && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} />
          <path d="M7 1.4a5.6 5.6 0 0 1 0 11.2Z" fill="currentColor" />
        </>
      )}
      {(status === 'EXECUTED' || status === 'PARTIAL') && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} strokeDasharray={status === 'PARTIAL' ? '3 2' : undefined} />
          <path d="M4.5 7.2 6.3 9 9.6 5.2" {...c} />
        </>
      )}
      {status === 'FAILED' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} />
          <path d="M4.8 4.8l4.4 4.4M9.2 4.8 4.8 9.2" {...c} />
        </>
      )}
      {(status === 'REJECTED' || status === 'EXPIRED' || status === 'SUPERSEDED') && (
        <>
          <circle cx="7" cy="7" r="5.6" {...c} strokeDasharray="2.2 2" />
          <path d="M4.6 7h4.8" {...c} />
        </>
      )}
    </svg>
  );
}

const STATE_TONE: Record<string, string> = {
  PROPOSED: 'var(--ink)',
  HELD: 'var(--warn)',
  PARTIAL: 'var(--warn)',
  FAILED: 'var(--crit)',
};

export function StateChip({ status, word }: { status: string; word?: string }) {
  return (
    <span className="chip" style={{ ['--c' as string]: STATE_TONE[status] ?? 'var(--ink-2)' }}>
      <StateGlyph status={status} />
      {word ?? STATE_WORD[status] ?? status}
    </span>
  );
}

/** "Expires in 9.8 h", warning tone under one hour. */
function Expiry({ p, ctx }: { p: Proposal; ctx: Ctx }) {
  const left = p.expires_tick - ctx.tick;
  if (left <= 0) return <span className={s.warnText}>Expired at tick {p.expires_tick}</span>;
  const h = spanHours(ctx, left);
  return (
    <span className={h < 1 ? s.warnText : 'muted'} title={`Expires at tick ${p.expires_tick}; now tick ${ctx.tick}`}>
      Expires in {hours(h)}
    </span>
  );
}

function Facts({ p, v, ctx, open }: { p: Proposal; v: CardView; ctx: Ctx; open: boolean }) {
  return (
    <div className={s.facts}>
      {v.lines.map((l, i) => {
        const route = ctx.routes[l.routeId];
        const closed = route && route.status !== 'AVAILABLE';
        const transit = l.transitTicks ?? route?.transit_ticks ?? null;
        const arrival = open && transit != null ? ctx.tick + transit : l.arrivalTick;
        return (
          <div key={`${l.depotId}-${l.routeId}`} className={s.factLine}>
            {v.lines.length > 1 && <div className={`label ${s.factLineTitle}`}>Line {i + 1} of {v.lines.length}</div>}
            <dl className={s.dl}>
              <div>
                <dt>From depot</dt>
                <dd>{pretty(l.depotId)}</dd>
              </div>
              <div className={s.wide}>
                <dt>Route</dt>
                <dd className="mono">
                  {l.routeId.replace(/^route-/, '')}
                  {closed && <span className={s.warnText}> {route.status.toLowerCase()}</span>}
                </dd>
              </div>
              <div>
                <dt>Litres</dt>
                <dd className="mono">{liters(l.liters)}</dd>
              </div>
              <div>
                <dt>Transit</dt>
                <dd className="mono">
                  {transit != null ? hours(spanHours(ctx, transit)) : 'n/a'}
                  {transit != null && <span className="faint"> {plural(transit, 'tick')}</span>}
                </dd>
              </div>
              <div>
                <dt>Arrives</dt>
                <dd className="mono">
                  {arrival != null ? clockOf(ctx, arrival) : 'n/a'}
                  {open && <span className="faint"> if sent now</span>}
                </dd>
              </div>
              {i === 0 && (
                <div>
                  <dt>Policy</dt>
                  <dd>{POLICY[p.policy] ?? p.policy}</dd>
                </div>
              )}
            </dl>
          </div>
        );
      })}
      {v.fallback && <p className={s.fallback}>Fallback plan: {v.fallback}</p>}
    </div>
  );
}

/** What happened to a proposal that is no longer waiting, read from the ledger lines. */
function Outcome({ p, v, ctx, fresh }: { p: Proposal; v: CardView; ctx: Ctx; fresh: boolean }) {
  const sentLines = v.lines.filter((l) => l.executed != null && ['SUBMITTED', 'IN_TRANSIT', 'ARRIVED', 'UNKNOWN'].includes(l.status));
  const sent = sentLines.reduce((t, l) => t + (l.executed ?? 0), 0);
  let head: string;
  switch (p.status) {
    case 'APPROVED':
    case 'EXECUTING':
      head = 'Sending now. The server is re-checking the plan against the live network.';
      break;
    case 'EXECUTED':
      head = `Dispatched ${liters(sent)}.`;
      break;
    case 'PARTIAL':
      head = `Partly sent: ${liters(sent)} of ${liters(v.total)}.`;
      break;
    case 'FAILED':
      head = 'Nothing was sent.';
      break;
    case 'HELD':
      head = `Held, not sent yet${p.status_reason ? `: ${p.status_reason}` : ''}. It is sent on its own when sending reopens, or you can reject it.`;
      break;
    case 'EXPIRED':
      head = `Expired before sending${p.status_reason ? `: ${p.status_reason}` : ''}.`;
      break;
    case 'REJECTED':
      head = 'Rejected. Nothing was sent.';
      break;
    default:
      head = STATE_WORD[p.status] ?? p.status;
  }
  const detail = v.lines.filter((l) => !(p.status === 'HELD' && l.status === 'PLANNED') && !(p.status === 'REJECTED'));
  return (
    <div className={s.outcome} role="status">
      <p className={s.outcomeHead}>{head}</p>
      {detail.length > 0 && (
        <ul className={s.outcomeLines}>
          {detail.map((l) => {
            const clipped = l.executed != null && l.executed < l.liters - 0.5;
            return (
              <li key={`${l.depotId}-${l.routeId}`}>
                <span className="mono">{pretty(l.depotId)}</span>: {lineState(l.status)}
                {l.executed != null && ` (${liters(l.executed)})`}
                {clipped && <strong> clipped from {liters(l.liters)}</strong>}
                {clipped && ', limits were re-checked at send time'}
                {l.failureCode && `. Reason: ${failureText(l.failureCode)}`}
              </li>
            );
          })}
        </ul>
      )}
      {fresh && sent > 0 && <p className={s.outcomeNote}>Sent. A shipment cannot be recalled.</p>}
    </div>
  );
}

export function ProposalCard(props: CardProps) {
  const { p, view: v, ctx, variant, busy, override, error, decided, pairLevel } = props;
  const open = isOpen(p.status);
  const sentence = actionSentence(p, v);
  const headingId = `proposal-${p.id}-title`;
  const degraded = ctx.mode === 'DEGRADED' || ctx.mode === 'STALE';
  const inFlight = busy != null;
  const conf = v.confidence;
  const lvl = pairLevel ?? v.level;
  const { collapsed } = props;
  const bodyId = `proposal-${p.id}-body`;
  const hasEffect = v.effect.before != null || v.hoursP90 != null || v.effect.after != null;

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (!open || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const t = e.target as HTMLElement;
    if (t.closest('input, textarea, select, [contenteditable="true"]')) return;
    const k = e.key.toLowerCase();
    if (k !== 'a' && k !== 'r') return;
    e.preventDefault();
    e.stopPropagation();
    // A short card only opens: nobody should decide without the reasons on screen.
    if (collapsed) props.onToggle?.(true);
    else props.onAct(k === 'a' ? 'approve' : 'reject');
  };

  const why = evidence(v, (t) => clockOf(ctx, t));
  const chips = why.length;
  const reason = !ctx.hasToken ? 'token' : inFlight ? 'busy' : null;
  const blockedId = `proposal-${p.id}-blocked`;
  const approveLabel = p.status === 'HELD' ? `Retry dispatch ${liters(v.total)}` : `Dispatch ${liters(v.total)}`;

  return (
    <article
      id={`proposal-${p.id}`}
      tabIndex={-1}
      className={`${s.card} ${variant === 'active' ? s.cardActive : ''}`}
      aria-labelledby={headingId}
      aria-keyshortcuts={open && !collapsed ? 'A R' : undefined}
      onKeyDown={onKeyDown}
      // Focusing the card itself (another panel's "open proposal", or a click on it) opens it.
      onFocus={(e) => collapsed && e.target === e.currentTarget && props.onToggle?.(true)}
      data-status={p.status}
    >
      <header className={s.head}>
        <h3 id={headingId} className={s.sentence}>{sentence}</h3>
        <div className={s.meta}>
          <StateChip status={p.status} />
          {lvl && <LevelChip level={lvl} />}
          <FuelTag fuel={p.fuel_type} />
          {open && <Expiry p={p} ctx={ctx} />}
          {props.onToggle && (
            <button
              type="button"
              className={`btn sm ${s.toggle}`}
              aria-expanded={!collapsed}
              aria-controls={collapsed ? undefined : bodyId}
              aria-describedby={headingId}
              // keeps the press from focusing the card first, which would open it and let the click close it again
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => props.onToggle?.(collapsed)}
            >
              {collapsed ? 'Show details' : 'Hide details'}
            </button>
          )}
        </div>
      </header>

      {variant === 'active' && <Outcome p={p} v={v} ctx={ctx} fresh={decided === 'approve'} />}

      {variant === 'open' && props.review && (
        <p className={s.review} role="note">
          <strong>Human review requested.</strong> {props.review}
        </p>
      )}

      {variant === 'open' && collapsed && (
        <>
          <p className={s.planAge}>Figures from when the plan was made: {planMadeAt(p, ctx)}.</p>
          <p className={s.brief}>
            {conf.level} confidence.
            {hasEffect && ` Cover at high demand (P90): ${cover(v.effect.before ?? v.hoursP90)}; if sent: ${cover(v.effect.after)}.`}
          </p>
        </>
      )}

      {variant === 'open' && !collapsed && (
        <div id={bodyId} className={s.body}>
          <Facts p={p} v={v} ctx={ctx} open />

          <p className={s.planAge}>Confidence, Why, If ignored and Other options use figures from when the plan was made: {planMadeAt(p, ctx)}.</p>

          <div className={s.rows}>
            <div className="label">Confidence</div>
            <div>
              <span className={s.confWord}>{conf.level}</span>
              <span className={`mono muted ${s.confNum}`}>{conf.score.toFixed(2)}</span>
              <span className="muted"> {CONFIDENCE_ADVICE[conf.level]}</span>
              {conf.reasons.length > 0 && <div className={`faint ${s.reasons}`}>{conf.reasons.join('; ')}</div>}
            </div>

            <div className="label">Why</div>
            <div>
              {v.headline && <p>{v.headline}</p>}
              {chips > 0 && (
                <ul className={s.evidence} aria-label="Evidence">
                  {why.map((e) => (
                    <li key={e.key}>{e.text}</li>
                  ))}
                </ul>
              )}
            </div>

            <div className="label">If ignored</div>
            <p>
              {hasEffect ? (
                <>
                  Cover is {cover(v.effect.before ?? v.hoursP90)} at high demand (P90). Sending lifts it to {cover(v.effect.after)}
                  {v.effect.levelAfter && (
                    <>
                      {' '}
                      <span className={s.inlineChip}><LevelChip level={v.effect.levelAfter} /></span>
                    </>
                  )}
                  {v.effect.avoided != null && v.effect.avoided > 0 && (
                    <>
                      {' '}and avoids {liters(v.effect.avoided)} of unmet demand over the next {hours(spanHours(ctx, v.effect.windowTicks ?? 48))}.
                    </>
                  )}
                </>
              ) : (
                <span className="muted">No effect estimate on this proposal.</span>
              )}
            </p>
          </div>

          <details className={s.more}>
            <summary>Other options considered ({v.alternatives.length})</summary>
            {v.alternatives.length === 0 ? (
              <p className="muted">The planner listed no alternatives for this proposal.</p>
            ) : (
              <ul className={s.alts}>
                {v.alternatives.map((a, i) => (
                  <li key={`${a.option}-${i}`}>
                    <div className={s.altTitle}>
                      {altTitle(a)}
                      {a.arrivalTick != null && <span className="muted"> (arrives {clockOf(ctx, a.arrivalTick)})</span>}
                    </div>
                    {a.note && <div className="faint">{a.note}</div>}
                    {a.outlook && (
                      <div className={s.altOutlook}>
                        {a.outlook.level && <LevelChip level={a.outlook.level} />}
                        <span>P90 cover {cover(a.outlook.hoursP90)}</span>
                        {a.outlook.unmet != null && <span>unmet demand {liters(a.outlook.unmet)}</span>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {v.notes.length > 0 && <p className={`faint ${s.notes}`}>Planner notes: {v.notes.join('; ')}.</p>}
            <p className={`faint ${s.notes}`}>Plan made at tick {p.snapshot_tick}, {clockOf(ctx, p.snapshot_tick)}. Quantity can only shrink when sent: the server re-checks route, stock and tank room.</p>
          </details>

          {p.status === 'HELD' && <Outcome p={p} v={v} ctx={ctx} fresh={false} />}

          <div className={s.actions}>
            {degraded && (
              <label className={s.override}>
                <input type="checkbox" checked={override} onChange={(e) => props.onOverride(e.target.checked)} />
                <span>
                  Send even though data is stale
                  {ctx.mode === 'STALE' && <span className="faint"> (the server still holds sends while data is older than its limit)</span>}
                </span>
              </label>
            )}
            {!ctx.gateOpen && <p className={s.warnText}>Sending is closed on the server right now. An approval is held and sent when it reopens.</p>}
            {!ctx.hasToken && (
              <p className={s.tokenLine} id={blockedId}>
                Enter the operator token to approve or reject{' '}
                <button type="button" className="btn sm" onClick={props.onAskToken}>
                  Enter token
                </button>
              </p>
            )}
            <div className={s.buttons}>
              <button
                type="button"
                className={`btn primary ${reason ? s.blocked : ''}`}
                data-act="approve"
                aria-disabled={reason ? true : undefined}
                aria-describedby={reason === 'token' ? blockedId : undefined}
                aria-keyshortcuts="A"
                onClick={() => !reason && props.onAct('approve')}
              >
                {busy === 'approve' ? 'Sending…' : approveLabel}
                <span className="kbd" aria-hidden="true">A</span>
              </button>
              <button
                type="button"
                className={`btn ${reason ? s.blocked : ''}`}
                data-act="reject"
                aria-disabled={reason ? true : undefined}
                aria-describedby={reason === 'token' ? blockedId : undefined}
                aria-keyshortcuts="R"
                onClick={() => !reason && props.onAct('reject')}
              >
                {busy === 'reject' ? 'Rejecting…' : 'Reject'}
                <span className="kbd" aria-hidden="true">R</span>
              </button>
              {inFlight && (
                <span className={`muted ${s.busyText}`} role="status">
                  {busy === 'approve' ? 'Waiting for the server to re-check and send. Buttons are off until it answers.' : 'Waiting for the server. Buttons are off until it answers.'}
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className={s.error} role="alert">
          <LevelGlyph level="CRITICAL" />
          <div>
            <strong>{error.headline}</strong>
            {error.detail && <div className="mono">{error.detail}</div>}
          </div>
          <button type="button" className="btn ghost sm" onClick={props.onClearError}>
            Dismiss
          </button>
        </div>
      )}

      {props.onDismiss && (
        <div className={s.dismiss}>
          <button type="button" className="btn sm" onClick={props.onDismiss}>
            Dismiss
          </button>
        </div>
      )}
    </article>
  );
}

export function HistoryRow({ p, v }: { p: Proposal; v: CardView }) {
  return (
    <li className={s.histRow} id={`proposal-${p.id}`} tabIndex={-1}>
      <div className={s.histTop}>
        <StateChip status={p.status} />
        <span className="mono faint">#{p.id} tick {p.snapshot_tick}</span>
      </div>
      <div className={s.histSentence}>{actionSentence(p, v)}</div>
      {p.status_reason && <div className="muted">Reason: {p.status_reason}</div>}
      {v.lines.some((l) => l.failureCode) && (
        <div className="muted">
          {v.lines
            .filter((l) => l.failureCode)
            .map((l) => `${pretty(l.depotId)}: ${failureText(l.failureCode)}`)
            .join('; ')}
        </div>
      )}
      {v.lines.some((l) => l.executed != null) && <div className="muted">Sent {int(v.lines.reduce((t, l) => t + (l.executed ?? 0), 0))} L of {int(v.total)} L approved.</div>}
    </li>
  );
}
