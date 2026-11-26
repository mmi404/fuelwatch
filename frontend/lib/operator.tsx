'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { ApiError, send } from './api';

interface Operator {
  /** The shared secret sent as X-Operator-Token. Empty until the operator enters it (or the build supplied a default). */
  token: string;
  setToken: (t: string) => void;
  /** Whether the token dialog is open (the shell renders it). */
  askOpen: boolean;
  ask: (open: boolean) => void;
  /** Last write error, shown inline by whoever started the write. 401 also opens the token dialog. */
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
        if (e instanceof ApiError && (e.status === 401 || e.status === 503)) ask(true);
        throw e;
      }
    },
    [token],
  );

  const value = useMemo(() => ({ token, setToken, askOpen, ask, write }), [token, setToken, askOpen, write]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
