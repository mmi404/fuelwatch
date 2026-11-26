'use client';

import { useEffect, useRef, useState } from 'react';
import { fuelName, pretty } from '@/lib/format';
import { riskKey } from '@/lib/risk';
import { useLive } from '@/lib/world';

const THROTTLE_MS = 10_000;
const ALERT_CLEAR_MS = 8_000;

/**
 * One polite status region (present from first render) plus an alert element that exists only while a new CRITICAL is announced.
 * Silent on the first load and on the first data after a connection error (a reconnect snapshot is not news).
 */
export function LiveRegion() {
  const { risks, error } = useLive();
  const [polite, setPolite] = useState('');
  const [alert, setAlert] = useState('');
  const baseline = useRef<{ sig: string; crit: Set<string> } | null>(null);
  const sawError = useRef(false);
  const lastAt = useRef(0);
  const pending = useRef<{ text: string; timer: ReturnType<typeof setTimeout> } | null>(null);
  const alertTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (error) sawError.current = true;
  }, [error]);

  useEffect(() => {
    if (error || risks.length === 0) return;
    const crit = risks.filter((r) => r.level === 'CRITICAL');
    const warn = risks.filter((r) => r.level === 'WARNING').length;
    const critKeys = new Set(crit.map(riskKey));
    const text = `${crit.length} critical, ${warn} warning, ${risks.length} pairs`;
    const prev = baseline.current;
    baseline.current = { sig: text, crit: critKeys };
    if (!prev || sawError.current) {
      sawError.current = false;
      return;
    }

    const fresh = crit.filter((r) => !prev.crit.has(riskKey(r)));
    if (fresh.length) {
      const names = fresh.map((r) => `${pretty(r.station_id)} ${fuelName(r.fuel_type).toLowerCase()}`);
      setAlert(`New critical: ${names.length > 2 ? `${names[0]} and ${names.length - 1} more` : names.join(' and ')}`);
      clearTimeout(alertTimer.current);
      alertTimer.current = setTimeout(() => setAlert(''), ALERT_CLEAR_MS);
    }

    if (text === prev.sig) return;
    const wait = THROTTLE_MS - (Date.now() - lastAt.current);
    const say = (t: string) => {
      lastAt.current = Date.now();
      pending.current = null;
      setPolite(t);
    };
    if (wait <= 0) say(text);
    else {
      if (pending.current) clearTimeout(pending.current.timer);
      pending.current = { text, timer: setTimeout(() => say(text), wait) };
    }
  }, [risks, error]);

  useEffect(
    () => () => {
      clearTimeout(alertTimer.current);
      if (pending.current) clearTimeout(pending.current.timer);
    },
    [],
  );

  return (
    <>
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {polite}
      </div>
      {alert && (
        <div role="alert" className="sr-only">
          {alert}
        </div>
      )}
    </>
  );
}
