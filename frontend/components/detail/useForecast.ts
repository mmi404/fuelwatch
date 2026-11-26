'use client';

import { useCallback, useEffect, useState } from 'react';
import { ForecastRow, get } from '@/lib/api';
import { FUTURE_TICKS } from './model';

interface State { pair: string; rows: ForecastRow[] | null; error: string | null; loading: boolean }

/**
 * GET /api/forecast for one pair. Refetches when `tick` changes and keeps the previous rows on screen while it loads
 * (and after a failed refresh). Rows of another pair are never shown.
 */
export function useForecast(stationId: string, fuel: string, tick: number | null) {
  const pair = `${stationId}:${fuel}`;
  const [state, setState] = useState<State>({ pair, rows: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (tick === null) return;
    let live = true;
    setState((s) => (s.pair === pair ? { ...s, loading: true } : { pair, rows: null, error: null, loading: true }));
    get<ForecastRow[]>(`/forecast?station_id=${encodeURIComponent(stationId)}&fuel=${encodeURIComponent(fuel)}&ticks=${FUTURE_TICKS}`)
      .then((rows) => {
        if (live) setState({ pair, rows, error: null, loading: false });
      })
      .catch((e: unknown) => {
        if (live) setState((s) => ({ pair, rows: s.pair === pair ? s.rows : null, error: e instanceof Error ? e.message : String(e), loading: false }));
      });
    return () => {
      live = false;
    };
  }, [stationId, fuel, pair, tick, nonce]);

  const retry = useCallback(() => setNonce((n) => n + 1), []);
  const mine = state.pair === pair;
  return { rows: mine ? state.rows : null, error: mine ? state.error : null, loading: mine ? state.loading : true, retry };
}
