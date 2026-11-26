import type { Level } from '@/lib/api';
import { levelColor } from '@/lib/risk';

export type LevelLike = Level | 'OUTAGE';

const LABEL: Record<LevelLike, string> = { CRITICAL: 'Critical', WARNING: 'Warning', WATCH: 'Watch', OK: 'OK', UNKNOWN: 'Unknown', OUTAGE: 'Outage' };
export const levelLabel = (l: LevelLike) => LABEL[l];

/** A distinct shape per level, so status never depends on colour alone. Stroke only, drawn in `currentColor`. */
export function LevelGlyph({ level, size = 14, title }: { level: LevelLike; size?: number; title?: string }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinejoin: 'round' as const, strokeLinecap: 'round' as const };
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true} focusable="false" style={{ color: levelColor(level), flex: 'none' }}>
      {level === 'OK' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...common} />
          <path d="M4.5 7.2 6.3 9 9.6 5.2" {...common} />
        </>
      )}
      {level === 'WATCH' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...common} />
          <circle cx="7" cy="7" r="1.6" fill="currentColor" />
        </>
      )}
      {level === 'WARNING' && (
        <>
          <path d="M7 1.8 13 12.2H1Z" {...common} />
          <path d="M7 5.6v3.2" {...common} />
          <circle cx="7" cy="10.3" r="0.5" fill="currentColor" stroke="currentColor" strokeWidth="0.5" />
        </>
      )}
      {level === 'CRITICAL' && (
        <>
          <path d="M4.6 1.2h4.8l3.4 3.4v4.8l-3.4 3.4H4.6L1.2 9.4V4.6Z" {...common} fill="currentColor" fillOpacity="0.14" />
          <path d="M7 4v3.6" {...common} />
          <circle cx="7" cy="9.7" r="0.5" fill="currentColor" stroke="currentColor" strokeWidth="0.5" />
        </>
      )}
      {level === 'UNKNOWN' && <circle cx="7" cy="7" r="5.6" {...common} strokeDasharray="2.2 2" />}
      {level === 'OUTAGE' && (
        <>
          <circle cx="7" cy="7" r="5.6" {...common} />
          <path d="M3.2 10.8 10.8 3.2" {...common} />
        </>
      )}
    </svg>
  );
}

/** Glyph + stamped word. The only way a level is shown as text. */
export function LevelChip({ level, solid = false, label }: { level: LevelLike; solid?: boolean; label?: string }) {
  return (
    <span className={`chip${solid ? ' solid' : ''}${level === 'UNKNOWN' ? ' dashed' : ''}`} style={{ ['--c' as string]: levelColor(level) }}>
      <LevelGlyph level={level} size={12} />
      {label ?? levelLabel(level)}
    </span>
  );
}
