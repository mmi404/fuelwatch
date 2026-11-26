import { fuelName } from '@/lib/format';
import { fuelColor, fuelLetter } from '@/lib/risk';

/** Outlined letter tile + name. Outlined on purpose: it must never read as a filled status badge. */
export function FuelTag({ fuel, short = false }: { fuel: string; short?: boolean }) {
  return (
    <span className="fuel" style={{ ['--c' as string]: fuelColor(fuel) }}>
      <span className="letter" aria-hidden="true">{fuelLetter(fuel)}</span>
      {short ? <span className="sr-only">{fuelName(fuel)}</span> : fuelName(fuel)}
    </span>
  );
}
