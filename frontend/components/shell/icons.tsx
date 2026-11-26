// Small stroke icons for the title block. All drawn in currentColor, decorative (aria-hidden); the control carries the name.

const base = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true, focusable: 'false' } as const;

/** Brand mark: a tank in section with its level line and gauge ticks. Original, flat, one colour. */
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true" focusable="false">
      <rect x="3.75" y="2.75" width="11.5" height="16.5" rx="1" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <rect x="4.5" y="10.5" width="10" height="8" fill="currentColor" />
      <path d="M17.5 5h2.5M17.5 8.2h1.5M17.5 11.4h2.5M17.5 14.6h1.5M17.5 17.8h2.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="butt" fill="none" />
    </svg>
  );
}

export const IconTheme = () => (
  <svg {...base}>
    <circle cx="8" cy="8" r="5.2" />
    <path d="M8 2.8v10.4A5.2 5.2 0 0 0 8 2.8Z" fill="currentColor" stroke="none" />
  </svg>
);

export const IconDensity = ({ compact }: { compact: boolean }) => (
  <svg {...base}>
    {compact ? <path d="M2.5 3.5h11M2.5 6.2h11M2.5 8.9h11M2.5 11.6h11M2.5 14.3h11" strokeWidth="1.2" /> : <path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h11" />}
  </svg>
);

/** Token held / not held: a key. */
export const IconKey = ({ set }: { set: boolean }) => (
  <svg {...base}>
    <circle cx="5.2" cy="8" r="2.7" strokeDasharray={set ? undefined : '2 1.6'} />
    <path d="M7.9 8h6M11.6 8v2.4M13.9 8v1.6" />
  </svg>
);

export const IconSearch = () => (
  <svg {...base}>
    <circle cx="7" cy="7" r="4.2" />
    <path d="m10.2 10.2 3.6 3.6" />
  </svg>
);

export const IconWarnTri = ({ size = 14 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true" focusable="false">
    <path d="M7 1.8 13 12.2H1Z" />
    <path d="M7 5.6v3.2" />
    <circle cx="7" cy="10.3" r="0.5" fill="currentColor" />
  </svg>
);

export const IconArrow = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d="M2 6h8M6.5 2.5 10 6l-3.5 3.5" />
  </svg>
);
