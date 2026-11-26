'use client';

import AuditTrail from '@/components/ledger/AuditTrail';
import InFlightStrip from '@/components/ledger/InFlightStrip';
import ShipmentTable from '@/components/ledger/ShipmentTable';
import { useAudit, useLines } from '@/components/ledger/useLedger';
import css from '@/components/ledger/Ledger.module.css';
import { useLive } from '@/lib/world';

/** Ledger: what was sent, what happened to it, and who decided. */
export default function LedgerPage() {
  const { snapshot, refresh } = useLive();
  const lines = useLines();
  const audit = useAudit();
  const world = snapshot?.world ?? null;
  return (
    <div className={css.page}>
      <h1 className="sr-only">Ledger</h1>
      <InFlightStrip world={world} allocations={world?.allocations ?? null} />
      <ShipmentTable
        state={lines}
        world={world}
        refresh={() => {
          refresh();
          lines.reload();
        }}
      />
      <AuditTrail
        state={audit}
        refresh={() => {
          refresh();
          audit.reload();
        }}
      />
    </div>
  );
}
