'use client';

import { useEffect, useRef, useState } from 'react';
import type { Live } from '@/lib/world';

export type Mode = 'LIVE' | 'DEGRADED' | 'STALE' | 'STARTING';
export type Tone = 'normal' | 'warn' | 'crit';

export const WARN_AFTER_S = 10;
export const CRIT_AFTER_S = 30;
export const POLL_S = 5;

export interface Health {
  mode: Mode;
  hasData: boolean;
  /** Age of what is on screen: the backend's own age at fetch plus seconds since we fetched it. */
  ageSec: number | null;
  /** Seconds since our last good refresh. */
  sinceOkSec: number | null;
  tone: Tone;
  /** Wall-clock "HH:MM:SS" of the last good refresh, null before the first. */
  lastOk: string | null;
  /** True when the board should be dimmed: mode STALE, or no good refresh for 30 s. */
  stale: boolean;
  /** Failed poll attempts since the last good refresh, estimated from the 5 s poll. */
  retries: number;
}

const p2 = (n: number) => String(n).padStart(2, '0');
export function clockLocal(ms: number): string {
  const d = new Date(ms);
  return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}

/** One shared second-resolution clock. Starts at mount so server and client markup agree. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export function deriveHealth(live: Pick<Live, 'status' | 'lastOkAt' | 'error'>, now: number): Health {
  const { status, lastOkAt, error } = live;
  if (!status || lastOkAt == null) {
    return { mode: 'STARTING', hasData: false, ageSec: null, sinceOkSec: null, tone: 'normal', lastOk: null, stale: false, retries: 0 };
  }
  const since = Math.max(0, (now - lastOkAt) / 1000);
  const ageSec = (status.age_seconds ?? 0) + since;
  let mode: Mode = status.mode;
  if (error && mode === 'LIVE') mode = 'DEGRADED';
  if (since >= CRIT_AFTER_S) mode = 'STALE';
  const tone: Tone = ageSec > CRIT_AFTER_S ? 'crit' : ageSec > WARN_AFTER_S ? 'warn' : 'normal';
  return {
    mode,
    hasData: true,
    ageSec,
    sinceOkSec: since,
    tone,
    lastOk: clockLocal(lastOkAt),
    stale: mode === 'STALE' || since > CRIT_AFTER_S,
    retries: error ? Math.max(1, Math.floor(since / POLL_S)) : 0,
  };
}

/**
 * Progress through the current tick, 0 to 1, from the wall-clock gap between the last two tick changes we saw.
 * null until we have seen two ticks (we cannot know the pace before that). `idle` when the simulator has not ticked for 2 intervals.
 */
export function useTickProgress(tick: number | null, now: number): { progress: number | null; idle: boolean } {
  const seen = useRef<{ tick: number; at: number; interval: number | null } | null>(null);
  const [, bump] = useState(0);
  useEffect(() => {
    if (tick == null) return;
    const prev = seen.current;
    const t = Date.now();
    if (!prev) seen.current = { tick, at: t, interval: null };
    else if (tick !== prev.tick) {
      const steps = tick - prev.tick;
      seen.current = { tick, at: t, interval: steps > 0 ? (t - prev.at) / steps : null };
    } else return;
    bump((n) => n + 1);
  }, [tick]);
  const s = seen.current;
  if (!s || !s.interval || s.interval <= 0) return { progress: null, idle: false };
  const f = (now - s.at) / s.interval;
  return { progress: Math.min(1, Math.max(0, f)), idle: f > 2 };
}
