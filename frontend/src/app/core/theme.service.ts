import { Injectable, computed, signal } from '@angular/core';

export type ThemeChoice = 'auto' | 'light' | 'dark';

const THEME_KEY = 'teglab.theme';

/** Validated categorical palette (dataviz reference instance), fixed slot order. */
export const SERIES_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
export const SERIES_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
/** Sequential blue ramp 100 -> 700 (magnitude): low values recede toward the light surface. */
export const SEQUENTIAL_BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
/** Dark mode flips the anchor: low values recede toward the dark surface, high values are the brightest. */
export const SEQUENTIAL_BLUE_DARK = [...SEQUENTIAL_BLUE].reverse();

export interface ChartInk {
  surface: string;
  text: string;
  secondary: string;
  muted: string;
  grid: string;
  axis: string;
  series: string[];
  /** sequential ramp, low -> high magnitude */
  sequential: string[];
}

const LIGHT: ChartInk = {
  surface: '#fcfcfb',
  text: '#0b0b0b',
  secondary: '#52514e',
  muted: '#898781',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  series: SERIES_LIGHT,
  sequential: SEQUENTIAL_BLUE,
};

const DARK: ChartInk = {
  surface: '#1a1a19',
  text: '#ffffff',
  secondary: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  series: SERIES_DARK,
  sequential: SEQUENTIAL_BLUE_DARK,
};

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly choice = signal<ThemeChoice>(this.readChoice());
  private readonly systemDark = signal(this.mediaDark());
  readonly dark = computed(() => (this.choice() === 'auto' ? this.systemDark() : this.choice() === 'dark'));
  readonly ink = computed<ChartInk>(() => (this.dark() ? DARK : LIGHT));

  constructor() {
    this.apply(this.choice());
    try {
      globalThis.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', (e) => this.systemDark.set(e.matches));
    } catch {
      /* no matchMedia (tests / old browsers) */
    }
  }

  cycle(): void {
    const next: ThemeChoice = this.choice() === 'auto' ? 'light' : this.choice() === 'light' ? 'dark' : 'auto';
    this.choice.set(next);
    this.apply(next);
    try {
      globalThis.localStorage?.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
  }

  private apply(choice: ThemeChoice): void {
    const root = globalThis.document?.documentElement;
    if (!root) {
      return;
    }
    if (choice === 'auto') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', choice);
    }
  }

  private readChoice(): ThemeChoice {
    try {
      const v = globalThis.localStorage?.getItem(THEME_KEY);
      return v === 'light' || v === 'dark' ? v : 'auto';
    } catch {
      return 'auto';
    }
  }

  private mediaDark(): boolean {
    try {
      return !!globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches;
    } catch {
      return false;
    }
  }
}
