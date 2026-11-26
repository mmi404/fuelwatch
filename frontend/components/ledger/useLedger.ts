'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AuditRow, LedgerLine, get } from '@/lib/api';
import { useLive } from '@/lib/world';

const LINE_LIMIT = 200;
export const AUDIT_PAGE = 100;
const HEARTBEAT_MS = 5000;

/**
 * Changes whenever the ledger could have changed: a new tick, a proposal changing state, the autopilot, a new epoch.
 * Some audit rows (pause, for one) move none of those, so a successful poll every few seconds counts too.
 */
function useTrigger(): string {
  const { status, lastOkAt } = useLive();
  const beat = Math.floor((lastOkAt ?? 0) / HEARTBEAT_MS);
  return useMemo(
    () => `${beat}|${status?.epoch?.id ?? '-'}|${status?.tick ?? '-'}|${JSON.stringify(status?.proposals_by_status ?? {})}|${status?.autopilot?.enabled ?? '-'}`,
    [beat, status],
  );
}

/** Ids that arrived (or changed status) since the last load, kept for 1.6 s so the row can flash once. Never on the first load. */
function useFresh() {
  const [fresh, setFresh] = useState<ReadonlySet<number>>(new Set());
  const seen = useRef<Map<number, string> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const note = useCallback((items: { id: number; sig: string }[]) => {
    const prev = seen.current;
    seen.current = new Map(items.map((i) => [i.id, i.sig]));
    if (prev === null) return;
    const hit = items.filter((i) => prev.get(i.id) !== i.sig).map((i) => i.id);
    if (!hit.length) return;
    setFresh(new Set(hit));
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setFresh(new Set()), 1600);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return { fresh, note };
}

export interface LedgerState {
  lines: LedgerLine[];
  loaded: boolean;
  error: string | null;
  okAt: number | null;
  fresh: ReadonlySet<number>;
  reload: () => void;
}

/** GET /api/allocations?limit=200, newest first. A failed fetch keeps the old rows and sets `error`. */
export function useLines(): LedgerState {
  const trigger = useTrigger();
  const [lines, setLines] = useState<LedgerLine[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okAt, setOkAt] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const { fresh, note } = useFresh();
  const sent = useRef(0);
  const applied = useRef(0);

  // An answer is used unless a later request has already answered. Dropping it whenever a later one was merely sent
  // would show nothing at all once replies take longer than the gap between requests.
  useEffect(() => {
    const mine = ++sent.current;
    get<LedgerLine[]>(`/allocations?limit=${LINE_LIMIT}`)
      .then((rows) => {
        if (mine <= applied.current) return;
        applied.current = mine;
        const sorted = [...rows].sort((a, b) => b.id - a.id);
        note(sorted.map((r) => ({ id: r.id, sig: r.status })));
        setLines(sorted);
        setLoaded(true);
        setError(null);
        setOkAt(Date.now());
      })
      .catch((e: unknown) => {
        if (mine <= applied.current) return;
        applied.current = mine;
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [trigger, nonce, note]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { lines, loaded, error, okAt, fresh, reload };
}

export interface AuditState {
  rows: AuditRow[];
  loaded: boolean;
  error: string | null;
  okAt: number | null;
  fresh: ReadonlySet<number>;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

/** GET /api/audit?limit=100, newest first. Refresh merges the first page by id; "load older" pages with `offset`. */
export function useAudit(): AuditState {
  const trigger = useTrigger();
  const [byId, setById] = useState<Map<number, AuditRow>>(new Map());
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okAt, setOkAt] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nonce, setNonce] = useState(0);
  const { fresh, note } = useFresh();
  const sent = useRef(0);
  const applied = useRef(0);
  const count = useRef(0);
  const newest = useRef(0);

  const merge = useCallback((rows: AuditRow[], replace: boolean) => {
    setById((prev) => {
      const next = replace ? new Map<number, AuditRow>() : new Map(prev);
      for (const r of rows) next.set(r.id, r);
      count.current = next.size;
      return next;
    });
  }, []);

  useEffect(() => {
    const mine = ++sent.current;
    get<AuditRow[]>(`/audit?limit=${AUDIT_PAGE}&offset=0`)
      .then((rows) => {
        if (mine <= applied.current) return;
        applied.current = mine;
        note(rows.map((r) => ({ id: r.id, sig: '' })));
        const top = rows[0]?.id ?? 0;
        const first = count.current === 0;
        // Lower ids than before: the backend's database was replaced. A full page that starts above the last id we
        // hold: more rows arrived than one page carries. Either way, merging would hide a hole, so start again.
        const replaced = top < newest.current;
        const gap = !first && rows.length === AUDIT_PAGE && rows[rows.length - 1].id > newest.current + 1;
        merge(rows, replaced || gap);
        newest.current = top;
        if (first || replaced || gap) setHasMore(rows.length === AUDIT_PAGE);
        setLoaded(true);
        setError(null);
        setOkAt(Date.now());
      })
      .catch((e: unknown) => {
        if (mine <= applied.current) return;
        applied.current = mine;
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [trigger, nonce, merge, note]);

  const rows = useMemo(() => [...byId.values()].sort((a, b) => b.id - a.id), [byId]);

  const loadMore = useCallback(() => {
    setLoadingMore(true);
    get<AuditRow[]>(`/audit?limit=${AUDIT_PAGE}&offset=${count.current}`)
      .then((older) => {
        merge(older, false);
        setHasMore(older.length === AUDIT_PAGE);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoadingMore(false));
  }, [merge]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { rows, loaded, error, okAt, fresh, hasMore, loadingMore, loadMore, reload };
}
