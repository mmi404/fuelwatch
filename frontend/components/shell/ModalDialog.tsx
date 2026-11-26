'use client';

import { useEffect, useRef } from 'react';
import s from './Shell.module.css';

/**
 * A native modal <dialog>: the browser makes the rest of the page inert (focus trap) and handles Esc.
 * Children are mounted only while open, so each opening starts fresh. Focus returns to whatever held it before.
 * Put `data-autofocus` on the control that should take focus first.
 */
export function ModalDialog({
  open,
  onClose,
  labelledBy,
  className = '',
  children,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  className?: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      d.showModal();
      d.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);

  const handleClose = () => {
    onClose();
    const el = returnTo.current;
    returnTo.current = null;
    // Do not pull focus out of a dialog that opened in the meantime (a palette command can open the token dialog).
    if (el && el.isConnected && !document.querySelector('dialog[open]')) el.focus({ preventScroll: true });
  };

  return (
    <dialog
      ref={ref}
      className={`${s.modal} ${className}`}
      aria-labelledby={labelledBy}
      onClose={handleClose}
      onClick={(e) => {
        if (e.target === ref.current) ref.current?.close();
      }}
    >
      {open && <div className={s.modalInner}>{children}</div>}
    </dialog>
  );
}
