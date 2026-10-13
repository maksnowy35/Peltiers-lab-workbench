import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { NumPipe, PctPipe } from '../../core/format';
import { CalibrationParam, CalibrationResult, Characterization, PairDesign } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { DesignEditorComponent } from '../../shared/design-editor.component';
import { StatTile } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

const QUANTITY_LABELS: Record<string, string> = {
  voltage: 'U',
  power: 'P',
  q_hot: 'Q_h',
  q_cold: 'Q_c',
  t_hot_ext: 'T hot face',
  t_leg_hot: 'T leg hot',
  t_leg_cold: 'T leg cold',
  t_max: 'T max',
};

@Component({
  selector: 'app-model',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, DesignEditorComponent, PlotComponent, StatTile, NumPipe, PctPipe],
  templateUrl: './model.page.html',
  styleUrl: './model.page.css',
})
export class ModelPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly result = signal<Characterization | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly params = signal<CalibrationParam[]>([]);
  protected readonly fit = signal<string[]>(['hot_side_factor', 'cold_side_factor', 'r_el_extra_mohm']);
  protected readonly calibCases = signal<string[]>([]);
  protected readonly calibration = signal<CalibrationResult | null>(null);
  protected readonly calibrating = signal(false);
  protected readonly calibError = signal<string | null>(null);

  private timer: ReturnType<typeof setTimeout> | null = null;
  private runId = 0;

  protected readonly sweepU = computed<PlotTrace[]>(() => this.sweep('voltage_v'));
  protected readonly sweepP = computed<PlotTrace[]>(() => {
    const r = this.result();
    if (!r) {
      return [];
    }
    return [
      ...this.sweep('power_w'),
      { name: 'P_max', x: [r.max_power.current_a], y: [r.max_power.power_w], mode: 'markers', slot: 0, hollow: true, markerSize: 14, showInLegend: false },
    ];
  });
  protected readonly sweepEta = computed<PlotTrace[]>(() => this.sweep('efficiency'));

  protected readonly profileT = computed<PlotTrace[]>(() => {
    const r = this.result();
    const oc = r?.open_circuit.profiles;
    const mp = r?.max_power.profiles;
    if (!oc || !mp) {
      return [];
    }
    return [
      { name: 'open circuit', x: oc.P.x_mm, y: oc.P.t_c, slot: 0 },
      { name: `I = ${r.max_power.current_a.toFixed(3)} A (P_max)`, x: mp.P.x_mm, y: mp.P.t_c, slot: 1 },
    ];
  });

  protected readonly profileJoule = computed<PlotTrace[]>(() => {
    const mp = this.result()?.max_power.profiles;
    if (!mp) {
      return [];
    }
    return [
      { name: 'P leg', x: mp.P.x_el_mm, y: mp.P.joule_w_m3, slot: 0 },
      { name: 'N leg', x: mp.N.x_el_mm, y: mp.N.joule_w_m3, slot: 1 },
    ];
  });

  protected readonly dtSplit = computed<PlotTrace[]>(() => {
    const oc = this.result()?.open_circuit;
    if (!oc) {
      return [];
    }
    return [
      {
        type: 'bar',
        x: [0, 1, 2],
        y: [oc.dt_passive_hot_k, oc.dt_te_k, oc.dt_passive_cold_k],
        text: ['hot side: ceramic + Cu + contact', 'thermoelectric legs', 'cold side: contact + Cu + ceramic'],
        slot: 0,
      },
    ];
  });

  protected readonly dtTicks = {
    xaxis: { tickvals: [0, 1, 2], ticktext: ['hot passive', 'legs', 'cold passive'] },
  };

  protected readonly parity = computed<PlotTrace[]>(() => {
    const c = this.calibration();
    if (!c) {
      return [];
    }
    const obs: number[] = [];
    const before: number[] = [];
    const after: number[] = [];
    for (const [k, a] of c.after.entries()) {
      const b = c.before[k];
      const o = a.observed['voltage'] ?? [];
      const pa = a.predicted['voltage'] ?? [];
      const pb = b?.predicted['voltage'] ?? [];
      o.forEach((v, i) => {
        if (v !== null && pa[i] !== null && pa[i] !== undefined) {
          obs.push(v);
          after.push(pa[i] as number);
          before.push((pb[i] ?? NaN) as number);
        }
      });
    }
    const lo = Math.min(0, ...obs);
    const hi = Math.max(...obs, 1e-6);
    return [
      { name: 'y = x', x: [lo, hi], y: [lo, hi], mode: 'lines', muted: true, showInLegend: false },
      { name: 'before', x: obs, y: before, mode: 'markers', muted: true },
      { name: 'after', x: obs, y: after, mode: 'markers', slot: 0 },
    ];
  });

  protected readonly calibRows = computed(() => {
    const c = this.calibration();
    if (!c) {
      return [];
    }
    return c.fit.map((k) => ({
      key: k,
      label: this.params().find((p) => p.key === k)?.label ?? k,
      before: c.initial[k],
      after: c.params[k],
    }));
  });

  protected readonly errorRows = computed(() => {
    const c = this.calibration();
    if (!c) {
      return [];
    }
    return c.after.map((a, k) => ({
      name: a.name,
      items: Object.entries(a.errors).map(([q, e]) => ({
        q: QUANTITY_LABELS[q] ?? q,
        before: c.before[k]?.errors[q]?.rms_rel ?? null,
        after: e.rms_rel,
      })),
    }));
  });

  constructor() {
    effect(() => {
      const d = this.ws.design();
      if (!d) {
        return;
      }
      untracked(() => this.schedule(d));
    });
    effect(() => {
      const cases = this.ws.cases();
      if (cases.length && !untracked(() => this.calibCases().length)) {
        untracked(() => this.calibCases.set(cases.filter((c) => c.name.startsWith('A0-TT')).map((c) => c.id).slice(0, 1)));
      }
    });
    void this.api.calibrationParams().then((p) => this.params.set(p), () => this.params.set([]));
    inject(DestroyRef).onDestroy(() => {
      if (this.timer) {
        clearTimeout(this.timer);
      }
    });
  }

  private schedule(d: PairDesign): void {
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => void this.characterize(d), 250);
  }

  private async characterize(d: PairDesign): Promise<void> {
    const id = ++this.runId;
    this.busy.set(true);
    try {
      const r = await this.api.characterize(d, 31);
      if (id === this.runId) {
        this.result.set(r);
        this.error.set(null);
      }
    } catch (err) {
      if (id === this.runId) {
        this.error.set(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (id === this.runId) {
        this.busy.set(false);
      }
    }
  }

  private sweep(key: 'voltage_v' | 'power_w' | 'efficiency'): PlotTrace[] {
    const r = this.result();
    if (!r) {
      return [];
    }
    return [{ x: r.sweep.map((s) => s.current_a), y: r.sweep.map((s) => s[key]), slot: 0 }];
  }

  protected onDesignChange(d: PairDesign): void {
    this.ws.setDesign(d, 'Edited on the Model page');
  }

  protected resetDesign(): void {
    this.ws.resetDesign();
  }

  protected toggleFit(key: string, ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    this.fit.update((f) => (on ? [...new Set([...f, key])] : f.filter((k) => k !== key)));
  }

  protected toggleCalibCase(id: string, ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    this.calibCases.update((ids) => (on ? [...new Set([...ids, id])] : ids.filter((x) => x !== id)));
  }

  protected async calibrate(): Promise<void> {
    const d = this.ws.design();
    if (!d || !this.calibCases().length) {
      return;
    }
    this.calibrating.set(true);
    this.calibError.set(null);
    try {
      this.calibration.set(await this.api.calibrate(d, this.calibCases(), this.fit()));
    } catch (err) {
      this.calibError.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.calibrating.set(false);
    }
  }

  protected applyCalibration(): void {
    const c = this.calibration();
    if (!c) {
      return;
    }
    const names = this.ws.cases().filter((x) => this.calibCases().includes(x.id)).map((x) => x.name);
    this.ws.setDesign({ ...c.design, name: this.ws.design()?.name ?? c.design.name },
      `Calibrated on ${names.join(', ')} (RMS ${(c.rms_after * 100).toFixed(2)} %)`);
  }
}
