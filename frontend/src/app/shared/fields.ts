import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { Check } from '../core/models';

/** Labeled number input; emits on commit (change/enter), not on every keystroke. */
@Component({
  selector: 'app-num',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <label class="field">
      <span class="field-label">{{ label() }}</span>
      <span class="num-wrap">
        <input
          type="number"
          [value]="value() ?? ''"
          [attr.step]="step()"
          [attr.min]="min()"
          [attr.max]="max()"
          [disabled]="disabled()"
          (change)="commit($event)"
        />
        @if (unit()) {
          <span class="unit">{{ unit() }}</span>
        }
      </span>
      @if (hint()) {
        <span class="field-hint">{{ hint() }}</span>
      }
    </label>
  `,
  styles: `
    .num-wrap { display: flex; align-items: center; gap: 6px; }
    .num-wrap input { width: 100%; }
    .unit { color: var(--muted); font-size: 12px; white-space: nowrap; }
  `,
})
export class NumField {
  readonly label = input('');
  readonly value = input<number | null | undefined>(null);
  readonly unit = input('');
  readonly step = input<number | string>('any');
  readonly min = input<number | null>(null);
  readonly max = input<number | null>(null);
  readonly hint = input('');
  readonly disabled = input(false);
  readonly valueChange = output<number>();

  commit(ev: Event): void {
    const v = (ev.target as HTMLInputElement).valueAsNumber;
    if (Number.isFinite(v)) {
      this.valueChange.emit(v);
    }
  }
}

/** Stat tile: label, value (pre-formatted), unit and an optional hint line. */
@Component({
  selector: 'app-stat',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="stat">
      <span class="stat-label">{{ label() }}</span>
      <span class="stat-value">{{ value() }}<small>{{ unit() }}</small></span>
      @if (hint()) {
        <span class="stat-hint">{{ hint() }}</span>
      }
    </div>
  `,
  styles: `
    .stat { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border: 1px solid var(--line);
            border-radius: 8px; background: var(--surface); min-width: 0; }
    .stat-label { font-size: 12px; color: var(--ink-2); }
    .stat-value { font-size: 20px; font-weight: 600; letter-spacing: -0.01em; color: var(--ink); white-space: nowrap; }
    .stat-value small { font-size: 12px; font-weight: 500; color: var(--muted); margin-left: 4px; }
    .stat-hint { font-size: 11.5px; color: var(--muted); }
  `,
})
export class StatTile {
  readonly label = input('');
  readonly value = input('');
  readonly unit = input('');
  readonly hint = input('');
}

/** Status line for automatic checks: icon + label, never color alone. */
@Component({
  selector: 'app-checks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="checks">
      @for (c of checks(); track c.id) {
        <li>
          <span [class]="'badge ' + c.level">
            <span class="ico" aria-hidden="true">{{ icon(c.level) }}</span>{{ levelLabel(c.level) }}
          </span>
          <div>
            <strong>{{ c.title }}</strong>
            <p>{{ c.detail }}</p>
          </div>
        </li>
      } @empty {
        <li class="muted">No checks available for this case.</li>
      }
    </ul>
  `,
  styles: `
    .checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
    .checks li { display: grid; grid-template-columns: 74px 1fr; gap: 10px; align-items: start; }
    .checks strong { font-size: 13px; font-weight: 600; }
    .checks p { color: var(--ink-2); font-size: 12.5px; }
  `,
})
export class ChecksList {
  readonly checks = input<Check[]>([]);

  icon(level: string): string {
    return level === 'ok' ? '✓' : level === 'warn' ? '!' : level === 'error' ? '×' : 'i';
  }

  levelLabel(level: string): string {
    return level === 'ok' ? 'OK' : level === 'warn' ? 'Check' : level === 'error' ? 'Error' : 'Info';
  }
}
