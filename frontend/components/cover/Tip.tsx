'use client';

import { useCallback, useRef } from 'react';
import styles from './CoverBoard.module.css';

export interface TipLine {
  /** Leads: the figure, in strong type. */
  value: string;
  /** Follows: what the figure is. */
  label: string;
}

/**
 * One floating readout. Content is built with `textContent` only, so API strings can never inject markup.
 * Render `<div ref={ref} ... />` from `tipProps` outside any `container-type` element (containment would trap `position: fixed`).
 */
export function useTip() {
  const ref = useRef<HTMLDivElement>(null);

  const show = useCallback((title: string, lines: TipLine[], x: number, y: number) => {
    const el = ref.current;
    if (!el) return;
    el.replaceChildren();
    const head = document.createElement('div');
    head.className = styles.tipTitle;
    head.textContent = title;
    el.appendChild(head);
    for (const l of lines) {
      const row = document.createElement('div');
      row.className = styles.tipRow;
      const v = document.createElement('span');
      v.className = styles.tipValue;
      v.textContent = l.value;
      const t = document.createElement('span');
      t.className = styles.tipLabel;
      t.textContent = l.label;
      row.append(v, t);
      el.appendChild(row);
    }
    el.hidden = false;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = x + 14 + w > window.innerWidth - 8 ? Math.max(8, x - w - 14) : x + 14;
    const top = y + 16 + h > window.innerHeight - 8 ? Math.max(8, y - h - 12) : y + 16;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }, []);

  const hide = useCallback(() => {
    const el = ref.current;
    if (el) el.hidden = true;
  }, []);

  return { ref, show, hide };
}
