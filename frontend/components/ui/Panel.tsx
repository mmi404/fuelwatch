import { useId } from 'react';

/** A hairline-bound region with a drawing-sheet header: small uppercase label, optional right-aligned meta, optional actions. */
export function Panel({
  title,
  meta,
  actions,
  children,
  flush = false,
  className = '',
}: {
  title: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  /** No padding in the body (tables and lists that draw their own rows). */
  flush?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <section className={`panel ${className}`} aria-labelledby={id}>
      <header className="panel-head">
        <h2 id={id}>{title}</h2>
        {meta != null && <span className="meta">{meta}</span>}
        {actions}
      </header>
      <div className={flush ? undefined : 'panel-body'}>{children}</div>
    </section>
  );
}
