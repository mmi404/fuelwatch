'use client';

import { useId, useMemo, useState } from 'react';
import styles from './Sparkline.module.css';

export interface SparkHover {
  /** Index into `values` of the nearest reading. */
  index: number;
  value: number;
  x: number;
  /** Pointer position, for placing a tooltip. */
  clientX: number;
  clientY: number;
}

export interface SparklineProps {
  /** Readings in time order, oldest first. Only real observations: never pad or interpolate. */
  values: number[];
  /** X position of each reading (for example the tick). Defaults to the index. Spacing is proportional, so gaps show as gaps. */
  xs?: number[];
  /** Drawn size in px. The svg scales down uniformly to fit a narrower cell. Default 88 by 24. */
  width?: number;
  height?: number;
  /** Fixed y range. Default is the min and max of `values` (a sparkline shows shape, so it is not zero-based unless you pass `[0, max]`). */
  domain?: [number, number];
  /** Fewer readings than this draws the quiet placeholder instead of a line. Default 3. */
  minPoints?: number;
  /** What is plotted, used in the accessible name and the hidden table caption, e.g. "Mirpur petrol stock". */
  label: string;
  /** Formats a reading for the accessible summary and the hidden table, e.g. (v) => `${v} L`. */
  format?: (v: number) => string;
  /** Formats an x value for the hidden table, e.g. (t) => `tick ${t}`. */
  xLabel?: (x: number) => string;
  /** Column headings of the hidden table. */
  xName?: string;
  yName?: string;
  /** How many of the latest readings the hidden table lists. Default 24. */
  tableLimit?: number;
  /** Text of the placeholder. Default "collecting". */
  placeholder?: string;
  /** Pointer moves over the line (nearest reading) or leaves (null). Use it to drive a tooltip. */
  onPoint?: (p: SparkHover | null) => void;
  className?: string;
}

const PAD = 3;

/**
 * Pure SVG sparkline of observed values. 2 px line, a 10 % wash under it, the last reading marked with a dot.
 * With fewer than `minPoints` readings it shows a "collecting" placeholder, never invented data.
 * Accessible: the svg is `role="img"` with a summary, and a visually hidden table lists the latest readings.
 */
export default function Sparkline({
  values,
  xs,
  width = 88,
  height = 24,
  domain,
  minPoints = 3,
  label,
  format = (v) => String(Math.round(v)),
  xLabel = (x) => String(x),
  xName = 'Reading',
  yName = 'Value',
  tableLimit = 24,
  placeholder = 'collecting',
  onPoint,
  className = '',
}: SparklineProps) {
  const uid = useId();
  const [hover, setHover] = useState<number | null>(null);
  const n = values.length;

  const geo = useMemo(() => {
    if (n < minPoints) return null;
    const x = xs && xs.length === n ? xs : values.map((_, i) => i);
    const x0 = x[0];
    const x1 = x[n - 1];
    const lo = domain ? domain[0] : Math.min(...values);
    const hi = domain ? domain[1] : Math.max(...values);
    const innerW = width - PAD * 2;
    const innerH = height - PAD * 2;
    const px = x.map((v) => PAD + (x1 === x0 ? innerW / 2 : ((v - x0) / (x1 - x0)) * innerW));
    const py = values.map((v) => PAD + (hi === lo ? innerH / 2 : (1 - (v - lo) / (hi - lo)) * innerH));
    const line = px.map((p, i) => `${i ? 'L' : 'M'}${p.toFixed(1)} ${py[i].toFixed(1)}`).join('');
    const area = `${line}L${px[n - 1].toFixed(1)} ${height}L${px[0].toFixed(1)} ${height}Z`;
    return { x, px, py, line, area, lo: Math.min(...values), hi: Math.max(...values) };
  }, [values, xs, n, minPoints, domain, width, height]);

  if (!geo) {
    return (
      <span className={`${styles.ph} ${className}`} style={{ maxWidth: width, minHeight: height }}>
        {placeholder}
        <span className="sr-only">{`. ${label}: ${n} of ${minPoints} readings so far.`}</span>
      </span>
    );
  }

  const summary = `${label}: ${n} readings, from ${format(values[0])} to ${format(values[n - 1])}, low ${format(geo.lo)}, high ${format(geo.hi)}.`;
  const listed = Math.min(n, tableLimit);
  const from = n - listed;

  const locate = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * width;
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(geo.px[i] - sx) < Math.abs(geo.px[best] - sx)) best = i;
    setHover(best);
    onPoint?.({ index: best, value: values[best], x: geo.x[best], clientX: e.clientX, clientY: e.clientY });
  };
  const leave = () => {
    setHover(null);
    onPoint?.(null);
  };

  const hi = hover != null ? hover : null;
  return (
    <span className={`${styles.wrap} ${className}`} style={{ maxWidth: width }}>
      <svg
        className={styles.svg}
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={summary}
        onPointerMove={locate}
        onPointerLeave={leave}
      >
        <path d={geo.area} className={styles.area} />
        <path d={geo.line} className={styles.line} vectorEffect="non-scaling-stroke" />
        {hi != null && <line x1={geo.px[hi]} x2={geo.px[hi]} y1={0} y2={height} className={styles.cross} vectorEffect="non-scaling-stroke" />}
        <circle cx={geo.px[hi ?? n - 1]} cy={geo.py[hi ?? n - 1]} r={3} className={styles.dot} />
      </svg>
      <table className="sr-only" aria-labelledby={`${uid}-cap`}>
        <caption id={`${uid}-cap`}>{`${label}, latest ${listed} of ${n} readings`}</caption>
        <thead>
          <tr>
            <th scope="col">{xName}</th>
            <th scope="col">{yName}</th>
          </tr>
        </thead>
        <tbody>
          {values.slice(from).map((v, i) => (
            <tr key={geo.x[from + i]}>
              <td>{xLabel(geo.x[from + i])}</td>
              <td>{format(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </span>
  );
}
