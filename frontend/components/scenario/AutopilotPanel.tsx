'use client';

import { useId, useRef, useState } from 'react';
import { Panel } from '@/components/ui/Panel';
import { Autopilot } from '@/lib/api';
import { plural } from '@/lib/format';
import { useLive } from '@/lib/world';
import { useOperator } from '@/lib/operator';
import StepButton from './StepButton';
import { describeError } from './util';
import s from './Scenario.module.css';

type Key = Exclude<keyof Autopilot, 'enabled'>;

interface Spec {
  key: Key;
  label: string;
  rule: string;
  unit: string;
  check: (n: number) => string | null;
}

// Same limits as AutopilotBody in backend/fuelwatch/main.py.
const SPECS: Spec[] = [
  { key: 'min_confidence', label: 'Minimum confidence', rule: 'from 0 to 1', unit: '', check: (n) => (n >= 0 && n <= 1 ? null : 'Enter a number from 0 to 1.') },
  { key: 'max_liters_per_proposal', label: 'Most per proposal', rule: 'more than 0', unit: 'L', check: (n) => (n > 0 ? null : 'Enter litres greater than 0.') },
  { key: 'max_liters_per_tick', label: 'Most per tick', rule: 'more than 0', unit: 'L', check: (n) => (n > 0 ? null : 'Enter litres greater than 0.') },
  { key: 'max_proposals_per_tick', label: 'Proposals per tick', rule: 'whole number, 1 to 100', unit: '', check: (n) => (Number.isInteger(n) && n >= 1 && n <= 100 ? null : 'Enter a whole number from 1 to 100.') },
];

const show = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 3 });

/** "12000", "12,000" and "0.8" are numbers. "1,5" is not guessed at. */
function parse(text: string): number {
  const t = text.trim();
  if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(t)) return Number(t.replace(/,/g, ''));
  return /^(\d+(\.\d*)?|\.\d+)$/.test(t) ? Number(t) : NaN;
}

interface Note { ok: boolean; text: string }

export default function AutopilotPanel() {
  const { status, refresh } = useLive();
  const { write, token, ask } = useOperator();
  const ap = status?.autopilot ?? null;
  const gateOpen = status?.execution.gate_open ?? null;
  const waiting = status?.proposals_by_status.PROPOSED ?? 0;
  const [draft, setDraft] = useState<Partial<Record<Key, string>>>({});
  const [left, setLeft] = useState<Partial<Record<Key, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const inputs = useRef<Partial<Record<Key, HTMLInputElement | null>>>({});
  const uid = useId();

  const errorOf = (spec: Spec): string | null => {
    const text = draft[spec.key];
    if (text === undefined) return null;
    const n = parse(text);
    return Number.isFinite(n) ? spec.check(n) : 'Enter a number.';
  };
  /** Fields that would be sent, or that block sending. A value equal to the current one is neither. */
  const pending = SPECS.filter((sp) => {
    const text = draft[sp.key];
    return text !== undefined && ap != null && parse(text) !== ap[sp.key];
  });

  const allowed = () => {
    if (token) return true;
    ask(true);
    return false;
  };

  async function put(body: Partial<Autopilot>, done: (a: Autopilot) => string) {
    setBusy(true);
    setNote(null);
    try {
      const next = await write<Autopilot>('PUT', '/autopilot', body);
      setNote({ ok: true, text: done(next) });
      return true;
    } catch (e) {
      setNote({ ok: false, text: describeError(e) });
      return false;
    } finally {
      setBusy(false);
      refresh();
    }
  }

  const clear = () => {
    setDraft({});
    setLeft({});
    setSubmitted(false);
  };

  async function apply(ev: React.FormEvent) {
    ev.preventDefault();
    if (busy || !ap || pending.length === 0) return;
    setSubmitted(true);
    const bad = SPECS.find((sp) => errorOf(sp));
    if (bad) {
      inputs.current[bad.key]?.focus();
      return;
    }
    if (!allowed()) return;
    const body: Partial<Autopilot> = {};
    for (const sp of pending) body[sp.key] = parse(draft[sp.key] as string);
    const count = pending.length;
    if (await put(body, () => `Saved ${plural(count, 'limit')}.`)) clear();
  }

  const enabled = ap?.enabled ?? false;

  return (
    <Panel title="Autopilot" meta={ap ? (enabled ? 'approves by itself' : 'a person approves') : 'waiting for data'}>
      <div className="dimmable">
        <div className={s.apHead}>
          <p className={s.apState} data-on={enabled ? 'true' : 'false'}>
            Autopilot is <strong>{ap ? (enabled ? 'ON' : 'OFF') : 'unknown'}</strong>
          </p>
          {ap && (
            <div className={s.apAction}>
              <StepButton
                label={enabled ? 'Turn autopilot off' : 'Turn autopilot on…'}
                className={enabled ? 'btn danger' : 'btn'}
                needsConfirm={!enabled}
                confirmLabel="Turn on"
                busy={busy}
                allowed={allowed}
                onGo={() => void put({ enabled: !enabled }, (a) => `Autopilot is now ${a.enabled ? 'ON' : 'OFF'}.`)}
                warning={
                  <>
                    Autopilot will approve shipments with no one pressing a button, within the limits below. It starts within seconds, even while the
                    simulator is paused. {waiting === 0 ? 'No proposal is waiting now.' : `${plural(waiting, 'proposal is', 'proposals are')} waiting now.`}
                    {pending.length > 0 && <strong> Your unsaved changes are not used: save them first.</strong>}
                  </>
                }
              />
            </div>
          )}
        </div>

        <p className={s.gateLine}>
          {gateOpen == null
            ? 'Sending state unknown.'
            : gateOpen
              ? 'Sending is open: approvals go to the simulator.'
              : 'Sending is closed right now, so autopilot approves nothing.'}
        </p>
        <ul className={s.apCopy}>
          <li>It takes the most urgent open proposals that pass every limit, and the audit trail records each as &ldquo;autopilot&rdquo;.</li>
          <li>It approves nothing while the data is degraded or stale, or while the write breaker is open.</li>
          <li>Turning it off stops new approvals only. Shipments already approved, including held ones, still go ahead.</li>
          <li>It is off after every backend restart. A simulator reset does not turn it off.</li>
        </ul>
      </div>

      <form onSubmit={(e) => void apply(e)} noValidate aria-label="Autopilot limits">
        <table className={`tbl ${s.rails}`}>
          <thead>
            <tr>
              <th scope="col">Limit</th>
              <th scope="col" className="r">Now</th>
              <th scope="col">Change to</th>
            </tr>
          </thead>
          <tbody>
            {SPECS.map((sp) => {
              const err = left[sp.key] || submitted ? errorOf(sp) : null;
              const id = `${uid}-${sp.key}`;
              return (
                <tr key={sp.key}>
                  <th scope="row">
                    <label htmlFor={id}>{sp.label}</label>
                    <span className={s.rule} id={`${id}-rule`}>{sp.rule}</span>
                  </th>
                  <td className="num dimmable">
                    <span className={s.current}>{ap ? show(ap[sp.key]) : '--'}</span>
                    {ap && sp.unit && <span className="unit">{sp.unit}</span>}
                  </td>
                  <td>
                    <input
                      id={id}
                      ref={(el) => { inputs.current[sp.key] = el; }}
                      className={`field mono ${s.input}`}
                      inputMode="decimal"
                      autoComplete="off"
                      disabled={!ap}
                      value={draft[sp.key] ?? ''}
                      aria-invalid={err ? true : undefined}
                      aria-describedby={err ? `${id}-rule ${id}-err` : `${id}-rule`}
                      onChange={(e) => {
                        const v = e.target.value;
                        setDraft((d) => {
                          const next = { ...d };
                          if (v === '') delete next[sp.key];
                          else next[sp.key] = v;
                          return next;
                        });
                        setNote(null);
                      }}
                      onBlur={() => setLeft((t) => ({ ...t, [sp.key]: true }))}
                    />
                    {err && <span id={`${id}-err`} className={s.fieldErr}>{err}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {/* Pressing here must not blur the field first: its error would appear and push the button out from under the pointer. */}
        <div className={s.formRow} onMouseDown={(e) => e.preventDefault()}>
          <button type="submit" className="btn primary" aria-disabled={busy || pending.length === 0 || undefined}>Save limits</button>
          <button type="button" className="btn ghost" aria-disabled={busy || Object.keys(draft).length === 0 || undefined} onClick={() => { if (!busy) { clear(); setNote(null); } }}>Clear</button>
          <span className={s.hint}>{pending.length === 0 ? 'No changes to save.' : `${plural(pending.length, 'change')} to save.`}</span>
        </div>
      </form>

      <div className={s.resultSlot} role="status">
        {note && <p className={note.ok ? s.okLine : s.errLine}>{note.text}</p>}
      </div>
    </Panel>
  );
}
