import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { download, NumPipe, PctPipe } from '../../core/format';
import { LabCatalog, LabPlan, LabRequest } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { NumField, StatTile } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

type NumKey = {
  [K in keyof LabRequest]: LabRequest[K] extends number ? K : never;
}[keyof LabRequest];

@Component({
  selector: 'app-lab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, PlotComponent, NumField, StatTile, NumPipe, PctPipe],
  templateUrl: './lab.page.html',
  styleUrl: './lab.page.css',
})
export class LabPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly catalog = signal<LabCatalog | null>(null);
  protected readonly plan = signal<LabPlan | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly showSheet = signal(false);

  protected readonly form = signal<Omit<LabRequest, 'design'>>({
    n_pairs: 1,
    spare_fraction: 0.3,
    kerf_mm: 0.2,
    solder_id: 'sac305',
    joint_thickness_um: 50,
    fillet_factor: 1.3,
    paste_metal_mass_fraction: 0.885,
    ni_thickness_um: 2,
    ceramic_id: 'al2o3_96',
    cu_on_ceramic: 'dbc',
    test_mode: 'TT',
    t_hot_test_c: 100,
    t_cold_test_c: 20,
    q_hot_test_w: 0.3152,
    t_ambient_c: 22,
    supply_voltage_v: 24,
    heater_safety_factor: 2,
    heater_block_area_cm2: 6,
    emissivity: 0.3,
    h_conv: 6,
    insulation_loss_fraction: 0.2,
    coolant: 'water',
    coolant_dt_k: 1,
    joint_margin_k: 40,
    clamp_pressure_mpa: 0.5,
    sigma_t_k: 0.5,
    sigma_u_rel: 0.0005,
    sigma_u_abs_v: 0.000002,
    sigma_i_rel: 0.002,
    sigma_q_rel: 0.05,
  });

  protected readonly solder = computed(() => this.catalog()?.solders.find((s) => s.id === this.form().solder_id) ?? null);

  protected readonly sweepU = computed<PlotTrace[]>(() => {
    const p = this.plan();
    return p ? [{ x: p.electrical.expected_sweep.map((s) => s.current_a), y: p.electrical.expected_sweep.map((s) => s.voltage_v), mode: 'lines+markers', slot: 0 }] : [];
  });

  protected readonly sweepP = computed<PlotTrace[]>(() => {
    const p = this.plan();
    return p ? [{ x: p.electrical.expected_sweep.map((s) => s.current_a), y: p.electrical.expected_sweep.map((s) => s.power_w), mode: 'lines+markers', slot: 0 }] : [];
  });

  private started = false;

  constructor() {
    void this.api.labCatalog().then((c) => this.catalog.set(c), () => this.catalog.set(null));
    effect(() => {
      const d = this.ws.design();
      if (d && !this.started) {
        this.started = true;
        untracked(() => {
          const b = d.boundary;
          this.form.update((f) => ({
            ...f,
            test_mode: b.hot_mode === 'Q' ? 'QT' : 'TT',
            t_hot_test_c: b.hot_mode === 'T' ? b.t_hot_c : f.t_hot_test_c,
            q_hot_test_w: b.hot_mode === 'Q' ? b.q_hot_w : f.q_hot_test_w,
            t_cold_test_c: b.cold_mode === 'T' ? b.t_cold_c : f.t_cold_test_c,
          }));
          void this.run();
        });
      }
    });
  }

  protected set(key: NumKey, value: number): void {
    this.form.update((f) => ({ ...f, [key]: value }));
  }

  protected setSelect(key: 'solder_id' | 'ceramic_id' | 'cu_on_ceramic' | 'test_mode' | 'coolant', ev: Event): void {
    const v = (ev.target as HTMLSelectElement).value;
    this.form.update((f) => ({ ...f, [key]: v }) as Omit<LabRequest, 'design'>);
  }

  protected async run(): Promise<void> {
    const d = this.ws.design();
    if (!d) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      this.plan.set(await this.api.labPlan({ ...this.form(), design: d }));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(false);
    }
  }

  protected downloadSheet(): void {
    const p = this.plan();
    if (p) {
      download('teglab_run_sheet.md', p.run_sheet_md, 'text/markdown');
    }
  }

  protected downloadBom(): void {
    const p = this.plan();
    if (p) {
      download('teglab_bom.csv', p.bom_csv, 'text/csv');
    }
  }

  protected downloadTemplate(): void {
    const p = this.plan();
    if (p) {
      download('teglab_measurement_template.csv', p.measurement_template_csv, 'text/csv');
    }
  }
}
