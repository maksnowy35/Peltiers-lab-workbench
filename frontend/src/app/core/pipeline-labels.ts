import { ContactVerdict, Level, NodalKey, View } from './pipeline.models';

/** Exhaustive label maps: adding a key on the backend breaks the build here until it is labeled. */
export const NODAL_LABEL: Record<NodalKey, string> = {
  temperature_c: 'Temperature',
  voltage_v: 'Electric voltage',
  current_density_a_m2: 'Current density',
  joule_heat_w_m3: 'Joule heat',
  heat_flux_w_m2: 'Heat flux',
};

/** Compact symbols for tables: T, V, J, Joule heat, heat flux q. */
export const NODAL_SYMBOL: Record<NodalKey, string> = {
  temperature_c: 'T',
  voltage_v: 'V',
  current_density_a_m2: 'J',
  joule_heat_w_m3: 'Joule',
  heat_flux_w_m2: 'q',
};

export const VIEW_LABEL: Record<View, string> = {
  front: 'Front (x → , y ↑)',
  back: 'Back (−x → , y ↑)',
  right: 'Right (−z → , y ↑)',
  left: 'Left (z → , y ↑)',
  top: 'Top (x → , −z ↑)',
  bottom: 'Bottom (x → , z ↑)',
};

export type BadgeKind = 'ok' | 'warn' | 'error' | 'info';

export interface Badge {
  kind: BadgeKind;
  icon: string;
  text: string;
}

const LEVEL_BADGE: Record<Level, Badge> = {
  info: { kind: 'info', icon: 'i', text: 'Info' },
  warning: { kind: 'warn', icon: '!', text: 'Check' },
  error: { kind: 'error', icon: '×', text: 'Error' },
};

export function levelBadge(level: Level): Badge {
  return LEVEL_BADGE[level];
}

/** A bonded thermoelectric contact means the specified conductance was not applied: that is an error. */
export function verdictBadge(verdict: ContactVerdict, atActiveLayer: boolean): Badge {
  switch (verdict) {
    case 'as-specified':
      return { kind: 'ok', icon: '✓', text: 'As specified' };
    case 'bonded':
      return atActiveLayer
        ? { kind: 'error', icon: '×', text: 'Not applied' }
        : { kind: 'info', icon: 'i', text: 'Bonded' };
    case 'deviates':
      return { kind: 'warn', icon: '!', text: 'Deviates' };
    case 'not-evaluated':
      return { kind: 'info', icon: '-', text: 'n/a' };
  }
}
