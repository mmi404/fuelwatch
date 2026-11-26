'use client';

import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';
export type Density = 'comfortable' | 'compact';

const THEME_KEY = 'fuelwatch.theme';
const DENSITY_KEY = 'fuelwatch.density';

const root = () => document.documentElement;

function effectiveTheme(): Theme {
  const t = root().dataset.theme;
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
const effectiveDensity = (): Density => (root().dataset.density === 'compact' ? 'compact' : 'comfortable');

function store(key: string, value: string | null) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the choice still applies for this page */
  }
}

/** Theme and density. `null` until mounted (the first-paint script has already applied the saved values to <html>). */
export function usePrefs() {
  const [theme, setTheme] = useState<Theme | null>(null);
  const [density, setDensity] = useState<Density | null>(null);

  useEffect(() => {
    setTheme(effectiveTheme());
    setDensity(effectiveDensity());
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setTheme(effectiveTheme());
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = effectiveTheme() === 'dark' ? 'light' : 'dark';
    root().dataset.theme = next;
    store(THEME_KEY, next);
    setTheme(next);
  }, []);

  const toggleDensity = useCallback(() => {
    const next: Density = effectiveDensity() === 'compact' ? 'comfortable' : 'compact';
    if (next === 'compact') root().dataset.density = 'compact';
    else delete root().dataset.density;
    store(DENSITY_KEY, next === 'compact' ? 'compact' : null);
    setDensity(next);
  }, []);

  return { theme, density, toggleTheme, toggleDensity };
}
