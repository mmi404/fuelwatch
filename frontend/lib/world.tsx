'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Anomaly, DeliveryFinding, Proposal, Risk, Snapshot, Status, get } from './api';
import { pairKey } from './risk';

export interface StockPoint { tick: number; inventory: number }

export interface Live {
  status: Status | null;
  risks: Risk[];
  proposals: Proposal[];
  alerts: Alert[];
  /** Open demand anomalies. Empty when there are none, and also when they could not be read: only their section hides. */
  anomalies: Anomaly[];
  /** Open late-delivery and lost-fuel findings. Empty also when they could not be read, like `anomalies`. */
  deliveryChecks: DeliveryFinding[];
  snapshot: Snapshot | null;
  /** Message of the last failed refresh, cleared by the next good one. Data on screen is kept when this is set. */
  error: string | null;
  /** Wall-clock ms of the last good refresh (null before the first). Use it for the age counter. */
  lastOkAt: number | null;
  /** True while the server-sent event stream is connected. */
  streaming: boolean;
  /** Observed stock per pair since this page loaded (one point per tick). The backend has no history endpoint. */
  history: Record<string, StockPoint[]>;
  /** The selected pair (`pairKey`) or null. Shared so the palette, the board and the detail sheet agree. */
  selected: string | null;
  select: (key: string | null) => void;
  refresh: () => void;
}

const Ctx = createContext<Live | null>(null);

export function useLive(): Live {
  const v = useContext(Ctx);
  if (!v) throw new Error('useLive must be used inside <WorldProvider>');
  return v;
}

const HISTORY_TICKS = 192;

/**
 * Keeps the console current. The event stream says "something changed" and triggers a refresh; a 5 second timer covers a
 * stream that is down, so nothing depends on it. A failed refresh keeps the last data on screen and sets `error`.
 */
export function WorldProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<Pick<Live, 'status' | 'risks' | 'proposals' | 'alerts' | 'snapshot'>>({ status: null, risks: [], proposals: [], alerts: [], snapshot: null });
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [deliveryChecks, setDeliveryChecks] = useState<DeliveryFinding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [history, setHistory] = useState<Record<string, StockPoint[]>>({});
  const [selected, select] = useState<string | null>(null);
  const busy = useRef(false);
  const again = useRef(false);
  const lastTick = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (busy.current) {
      again.current = true;
      return;
    }
    busy.current = true;
    // On its own, not in the Promise.all below: a failure or a slow answer here must not hold back the live data.
    get<Anomaly[]>('/anomalies')
      .then((a) => setAnomalies(Array.isArray(a) ? a : []))
      .catch(() => setAnomalies([]));
    get<DeliveryFinding[]>('/delivery-checks')
      .then((d) => setDeliveryChecks(Array.isArray(d) ? d : []))
      .catch(() => setDeliveryChecks([]));
    try {
      const [status, risks, proposals, alerts, snapshot] = await Promise.all([
        get<Status>('/status'),
        get<Risk[]>('/risks'),
        get<Proposal[]>('/proposals?limit=60'),
        get<Alert[]>('/alerts'),
        get<Snapshot>('/snapshot'),
      ]);
      setData({ status, risks, proposals, alerts, snapshot });
      setError(null);
      setLastOkAt(Date.now());
      const tick = status.tick;
      const reset = lastTick.current !== null && tick < lastTick.current;
      if (reset || lastTick.current !== tick) {
        lastTick.current = tick;
        setHistory((h) => {
          const next: Record<string, StockPoint[]> = reset ? {} : { ...h };
          for (const st of snapshot.world.stations) {
            for (const [fuel, inv] of Object.entries(st.inventory)) {
              const k = pairKey(st.id, fuel);
              const prev = next[k] ?? [];
              if (prev.length && prev[prev.length - 1].tick === tick) continue;
              next[k] = [...prev, { tick, inventory: inv }].slice(-HISTORY_TICKS);
            }
          }
          return next;
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      busy.current = false;
      if (again.current) {
        again.current = false;
        void refresh();
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    let pending: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource('/api/stream');
    const poke = () => {
      clearTimeout(pending);
      pending = setTimeout(() => void refresh(), 250);
    };
    source.onopen = () => setStreaming(true);
    source.onerror = () => setStreaming(false);
    for (const name of ['snapshot', 'proposal', 'proposals', 'autopilot']) source.addEventListener(name, poke);
    return () => {
      clearInterval(timer);
      clearTimeout(pending);
      source.close();
    };
  }, [refresh]);

  const value = useMemo<Live>(
    () => ({ ...data, anomalies, deliveryChecks, error, lastOkAt, streaming, history, selected, select, refresh: () => void refresh() }),
    [data, anomalies, deliveryChecks, error, lastOkAt, streaming, history, selected, refresh],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
