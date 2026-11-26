/** The single list of keyboard shortcuts: the cheat sheet and the palette rows read from here. */
export interface Shortcut {
  id: string;
  /** Keys in order. A sequence (g then o) has `seq: true`. */
  keys: string[];
  seq?: boolean;
  label: string;
  group: 'Navigate' | 'Board' | 'View' | 'Help';
}

export const PAGES = [
  { id: 'go-overview', href: '/', label: 'Overview', key: 'o' },
  { id: 'go-ledger', href: '/ledger', label: 'Ledger', key: 'l' },
  { id: 'go-scenario', href: '/scenario', label: 'Scenario', key: 's' },
  { id: 'go-results', href: '/results', label: 'Results', key: 'r' },
] as const;

export const SHORTCUTS: Shortcut[] = [
  { id: 'palette', keys: ['Ctrl', 'K'], label: 'Open or close the command palette (Cmd K on a Mac)', group: 'Help' },
  { id: 'palette-slash', keys: ['/'], label: 'Open the command palette', group: 'Help' },
  { id: 'help', keys: ['?'], label: 'Show this list of shortcuts', group: 'Help' },
  ...PAGES.map<Shortcut>((p) => ({ id: p.id, keys: ['g', p.key], seq: true, label: `Go to ${p.label}`, group: 'Navigate' })),
  { id: 'next', keys: ['j'], label: 'Select the next pair in the ranked order', group: 'Board' },
  { id: 'prev', keys: ['k'], label: 'Select the previous pair in the ranked order', group: 'Board' },
  { id: 'clear', keys: ['Esc'], label: 'Clear the selected pair', group: 'Board' },
  { id: 'theme', keys: ['t'], label: 'Switch between light and dark theme', group: 'View' },
  { id: 'density', keys: ['d'], label: 'Switch between comfortable and compact rows', group: 'View' },
];

export const keysOf = (id: string): string[] | undefined => SHORTCUTS.find((s) => s.id === id)?.keys;
