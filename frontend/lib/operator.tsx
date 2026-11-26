'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { ApiError, send } from './api';

/** GET /api/me: whose key is held. */
export interface Me { name: string; role: 'operator' | 'supervisor' }

interface Operator {
  /** The shared secret sent as X-Operator-Token. Empty until the operator enters it (or the build supplied a default). */
  token: string;
  setToken: (t: string) => void;
  /** Null until the server has recognised the key. */
  me: Me | null;
  /** Whether the token dialog is open (the shell renders it). */
  askOpen: boolean;
  ask: (open: boolean) => void;
  /** Sends a write. The error is thrown for the caller to show inline; a 401 also opens the token dialog. */
  write: <T>(method: 'POST' | 'PUT', path: string, body?: unknown) => Promise<T>;
}

const KEY = 'fuelwatch.operator-token';
const Ctx = createContext<Operator | null>(null);

export const useOperator = (): Operator => {
  const v = useContext(Ctx);
  if (!v) throw new Error('useOperator must be used inside <OperatorProvider>');
  return v;
};

export function OperatorProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState('');
  const [askOpen, ask] = useState(false);
  const [me, setMe] = useState<Me | null>(null);

  // Only for showing who is signed in: the server checks the key and its role again on every write.
  useEffect(() => {
    setMe(null);
    if (!token) return;
    let stale = false;
    fetch('/api/me', { cache: 'no-store', headers: { 'X-Operator-Token': token } })
      .then((r) => (r.ok ? (r.json() as Promise<Me>) : null))
      .then((found) => {
        if (!stale) setMe(found);
      })
      .catch(() => undefined); // a key that is not a valid header value, or no answer: show no name
    return () => {
      stale = true;
    };
  }, [token]);

  useEffect(() => {
    try {
      setTokenState(localStorage.getItem(KEY) || process.env.NEXT_PUBLIC_DEFAULT_TOKEN || '');
    } catch {
      setTokenState(process.env.NEXT_PUBLIC_DEFAULT_TOKEN || '');
    }
  }, []);

  const setToken = useCallback((t: string) => {
    setTokenState(t);
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* private window: keep it in memory only */
    }
  }, []);

  const write = useCallback(
    async <T,>(method: 'POST' | 'PUT', path: string, body?: unknown): Promise<T> => {
      try {
        return await send<T>(method, path, token, body);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) ask(true); // a 503 is not about this token, so typing it again cannot help
        throw e;
      }
    },
    [token],
  );

  const value = useMemo(() => ({ token, setToken, me, askOpen, ask, write }), [token, setToken, me, askOpen, write]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
