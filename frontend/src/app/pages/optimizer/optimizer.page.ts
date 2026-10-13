import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { download, NumPipe, parseNumberList, PctPipe, range } from '../../core/format';
import {
  Boundary,
  Constraints,
  DesignPoint,
  ExportResult,
  GeometryMode,
  MapResult,
  Objective,
  PairDesign,
  SweepResult,
} from '../../core/models';
import { WorkspaceService, cloneDesign } from '../../core/workspace.service';
import { NumField, StatTile } from '../../shared/fields';
import { HeatmapSpec, PlotComponent, PlotTrace } from '../../shared/plot.component';

type BcChoice = 'design' | 'QT' | 'TT' | 'HX';

const OBJECTIVES: { key: Objective; label: string; unit: string; scale: number; field: keyof DesignPoint }[] = [
  { key: 'p_max', label: 'Maximum power', unit: 'mW', scale: 1000, field: 'p_max_w' },
  { key: 'eta_at_p_max', label: 'Efficiency at P_max', unit: '%', scale: 100, field: 'eta_at_p_max' },
  { key: 'power_per_volume', label: 'Power per TE volume', unit: 'mW/mm³', scale: 1, field: 'power_per_volume_mw_mm3' },
];

@Component({
  selector: 'app-optimizer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, PlotComponent, NumField, StatTile, NumPipe, PctPipe],
  templateUrl: './optimizer.page.html',
  styleUrl: './optimizer.page.css',
})
export class OptimizerPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly objectives = OBJECTIVES;
  protected readonly mode = signal<GeometryMode>('fixed_cold_base');
  protected readonly objective = signal<Objective>('p_max');
  protected readonly angleFrom = signal(-5);
  protected readonly angleTo = signal(20);
  protected readonly angleStep = signal(1);
  protected readonly ratioFrom = signal(0.3);
  protected readonly ratioTo = signal(1.5);
  protected readonly ratioStep = signal(0.05);
  protected readonly heightsText = signal('1.0, 1.4, 2.0');
  protected readonly mapHFrom = signal(0.8);
  protected readonly mapHTo = signal(3.0);
  protected readonly mapHStep = signal(0.2);
  protected readonly aMin = signal(0.5);
  protected readonly useJointLimit = signal(true);
  protected readonly jointMax = signal(180);
  protected readonly useMaterialLimit = signal(true);
  protected readonly thetaMax = signal<number | null>(null);

  protected readonly bc = signal<BcChoice>('design');
  protected readonly qHot = signal(0.3152);
  protected readonly tHot = signal(100);
  protected readonly tCold = signal(20);
  protected readonly tSource = signal(250);
  protected readonly rSource = signal(150);
  protected readonly tSink = signal(20);
  protected readonly rSink = signal(20);

  protected readonly sweep = signal<SweepResult | null>(null);
  protected readonly map = signal<MapResult | null>(null);
  protected readonly busy = signal<'sweep' | 'map' | null>(null);
  protected readonly error = signal<string | null>(null);

  protected readonly exportOpen = signal(false);
  protected readonly exportCurrents = signal('0, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 1.0');
  protected readonly paramMap = signal<Record<string, string>>({ a_hot_mm: 'P1', a_cold_mm: 'P2', height_mm: 'P3', current_a: 'P4' });
  protected readonly exportResult = signal<ExportResult | null>(null);

  protected readonly obj = computed(() => OBJECTIVES.find((o) => o.key === this.objective()) ?? OBJECTIVES[0]);
  protected readonly isVolume = computed(() => this.mode() === 'constant_volume');
  protected readonly xLabel = computed(() => (this.isVolume() ? 'side ratio a_h / a_c' : 'taper angle θ [°]'));
  protected readonly values = computed(() =>
    this.isVolume() ? range(this.ratioFrom(), this.ratioTo(), this.ratioStep()) : range(this.angleFrom(), this.angleTo(), this.angleStep()),
  );

  protected readonly constraints = computed<Constraints>(() => ({
    a_min_mm: this.aMin(),
    t_joint_max_c: this.useJointLimit() ? this.jointMax() : null,
    respect_material_limit: this.useMaterialLimit(),
    theta_max_deg: this.thetaMax(),
  }));

  protected readonly studyDesign = computed<PairDesign | null>(() => {
    const d = this.ws.design();
    if (!d) {
      return null;
    }
    const s = cloneDesign(d);
    const b: Boundary = s.boundary;
    switch (this.bc()) {
      case 'QT':
        Object.assign(b, { hot_mode: 'Q', cold_mode: 'T', q_hot_w: this.qHot(), t_cold_c: this.tCold() });
        break;
      case 'TT':
        Object.assign(b, { hot_mode: 'T', cold_mode: 'T', t_hot_c: this.tHot(), t_cold_c: this.tCold() });
        break;
      case 'HX':
        Object.assign(b, {
          hot_mode: 'HX', cold_mode: 'HX', t_source_c: this.tSource(), r_source_k_w: this.rSource(),
          t_sink_c: this.tSink(), r_sink_k_w: this.rSink(),
        });
        break;
      default:
        break;
    }
    return s;
  });

  protected readonly objectiveTraces = computed<PlotTrace[]>(() => {
    const r = this.sweep();
    if (!r) {
      return [];
    }
    const field = this.obj().field;
    const out: PlotTrace[] = [];
    r.series.forEach((s, k) => {
      const ok = s.points.filter((p) => p.feasible);
      const bad = s.points.filter((p) => !p.feasible);
      out.push({ name: `H = ${s.height_mm} mm`, x: ok.map((p) => p.x ?? null), y: ok.map((p) => num(p[field])), mode: 'lines+markers', slot: k, markerSize: 7 });
      if (bad.length) {
        out.push({
          name: `H = ${s.height_mm} mm · violates limits`, x: bad.map((p) => p.x ?? null), y: bad.map((p) => num(p[field])),
          mode: 'markers', slot: k, hollow: true, markerSize: 7, showInLegend: false, text: bad.map((p) => p.violations.join('; ')),
        });
      }
    });
    const o = r.optimum;
    if (o) {
      out.push({ name: 'optimum', x: [o.x ?? null], y: [num(o[field])], mode: 'markers', slot: 7, hollow: true, markerSize: 16, showInLegend: false });
    }
    return out;
  });

  protected readonly jointTraces = computed<PlotTrace[]>(() => {
    const r = this.sweep();
    if (!r) {
      return [];
    }
    return r.series.map((s, k) => ({
      name: `H = ${s.height_mm} mm`,
      x: s.points.map((p) => p.x ?? null),
      y: s.points.map((p) => p.t_joint_max_c),
      mode: 'lines' as const,
      slot: k,
    }));
  });

  /** y ranges from feasible designs so that absurd (infeasible) shapes do not squash the charts */
  protected readonly objectiveRange = computed(() => {
    const r = this.sweep();
    const f = this.obj().field;
    const vals = (r?.series ?? []).flatMap((s) => s.points.filter((p) => p.feasible).map((p) => num(p[f]))).filter((v): v is number => v !== null);
    if (!vals.length) {
      return {};
    }
    const scale = this.obj().scale;
    const hi = Math.max(...vals) * scale;
    const lo = Math.min(0, Math.min(...vals) * scale);
    return { yaxis: { range: [lo, hi * 1.25] } };
  });

  protected readonly jointRange = computed(() => {
    const r = this.sweep();
    const temps = (r?.series ?? []).flatMap((s) => s.points.filter((p) => p.feasible).map((p) => p.t_joint_max_c));
    const limit = this.constraints().t_joint_max_c;
    if (!temps.length) {
      return {};
    }
    const hi = Math.max(...temps, limit ?? 0);
    const lo = Math.min(...temps);
    return { yaxis: { range: [Math.max(0, lo - 10), hi * 1.3] } };
  });

  protected readonly jointShapes = computed<object[]>(() => {
    const limit = this.constraints().t_joint_max_c;
    if (limit === null) {
      return [];
    }
    return [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: limit, y1: limit, line: { color: '#898781', width: 1 } }];
  });

  protected readonly jointAnnotations = computed<object[]>(() => {
    const limit = this.constraints().t_joint_max_c;
    return limit === null
      ? []
      : [{ xref: 'paper', x: 1, y: limit, xanchor: 'right', yanchor: 'bottom', text: 'joint limit', showarrow: false, font: { size: 11, color: '#898781' } }];
  });

  protected readonly paretoTraces = computed<PlotTrace[]>(() => {
    const r = this.sweep();
    if (!r) {
      return [];
    }
    const all = r.series.flatMap((s) => s.points.filter((p) => p.feasible));
    return [
      { name: 'feasible designs', x: all.map((p) => p.te_volume_mm3), y: all.map((p) => p.p_max_w), mode: 'markers', muted: true, markerSize: 7 },
      { name: 'Pareto front', x: r.pareto.map((p) => p.te_volume_mm3), y: r.pareto.map((p) => p.p_max_w), mode: 'lines+markers', slot: 0, markerSize: 7 },
    ];
  });

  protected readonly heatmap = computed<HeatmapSpec | null>(() => {
    const m = this.map();
    if (!m) {
      return null;
    }
    const z = m.z.map((row, i) => row.map((v, j) => (m.feasible[i][j] ? v : null)));
    return { x: m.x, y: m.y, z, zTitle: `${this.obj().label} [${this.obj().unit}]`, zScale: this.obj().scale };
  });

  protected readonly mapOptimum = computed<PlotTrace[]>(() => {
    const o = this.map()?.optimum;
    if (!o) {
      return [];
    }
    return [{ name: 'optimum', x: [o.x ?? null], y: [o.height_mm], mode: 'markers', slot: 1, hollow: true, markerSize: 16, showInLegend: false }];
  });

  protected readonly gain = computed(() => {
    const r = this.sweep();
    if (!r?.optimum || !r.reference) {
      return null;
    }
    const f = this.obj().field;
    const a = num(r.optimum[f]);
    const b = num(r.reference[f]);
    return a !== null && b ? a / b - 1 : null;
  });

  constructor() {
    effect(() => {
      const d = this.ws.design();
      if (d && untracked(() => this.bc()) === 'design') {
        untracked(() => {
          this.qHot.set(d.boundary.q_hot_w);
          this.tHot.set(d.boundary.t_hot_c);
          this.tCold.set(d.boundary.t_cold_c);
        });
      }
    });
  }

  protected setMode(m: GeometryMode): void {
    this.mode.set(m);
    this.sweep.set(null);
    this.map.set(null);
  }

  protected setBc(ev: Event): void {
    this.bc.set((ev.target as HTMLSelectElement).value as BcChoice);
  }

  protected setObjective(ev: Event): void {
    this.objective.set((ev.target as HTMLSelectElement).value as Objective);
  }

  protected toggleJoint(ev: Event): void {
    this.useJointLimit.set((ev.target as HTMLInputElement).checked);
  }

  protected toggleMaterial(ev: Event): void {
    this.useMaterialLimit.set((ev.target as HTMLInputElement).checked);
  }

  protected setHeights(ev: Event): void {
    this.heightsText.set((ev.target as HTMLInputElement).value);
  }

  protected async runSweep(): Promise<void> {
    const d = this.studyDesign();
    if (!d) {
      return;
    }
    const heights = parseNumberList(this.heightsText()).filter((h) => h > 0).slice(0, 8);
    this.busy.set('sweep');
    this.error.set(null);
    try {
      this.sweep.set(
        await this.api.sweep({
          design: d,
          mode: this.mode(),
          values: this.values(),
          heights_mm: heights.length ? heights : null,
          objective: this.objective(),
          constraints: this.constraints(),
          volume_mm3: null,
        }),
      );
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(null);
    }
  }

  protected async runMap(): Promise<void> {
    const d = this.studyDesign();
    if (!d) {
      return;
    }
    const xs = this.values();
    const stride = Math.max(1, Math.ceil(xs.length / 31));
    const heights = range(this.mapHFrom(), this.mapHTo(), this.mapHStep()).slice(0, 31);
    this.busy.set('map');
    this.error.set(null);
    try {
      this.map.set(
        await this.api.map({
          design: d,
          mode: this.mode(),
          x_values: xs.filter((_, k) => k % stride === 0),
          heights_mm: heights,
          objective: this.objective(),
          constraints: this.constraints(),
          volume_mm3: null,
        }),
      );
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(null);
    }
  }

  protected useDesign(p: DesignPoint): void {
    const d = this.ws.design();
    if (!d) {
      return;
    }
    const n = cloneDesign(d);
    const ratio = (n.n_leg.a_cold_mm / n.p_leg.a_cold_mm) ** 2;
    const s = Math.sqrt(ratio);
    n.height_mm = round4(p.height_mm);
    n.p_leg.a_hot_mm = round4(p.a_hot_mm);
    n.p_leg.a_cold_mm = round4(p.a_cold_mm);
    n.n_leg.a_hot_mm = round4(p.a_hot_mm * s);
    n.n_leg.a_cold_mm = round4(p.a_cold_mm * s);
    n.name = `Optimum θ ${p.theta_deg.toFixed(1)}°, H ${p.height_mm} mm`;
    this.ws.setDesign(n, this.ws.calibrationNote());
  }

  protected setParam(key: string, ev: Event): void {
    const v = (ev.target as HTMLInputElement).value.trim();
    this.paramMap.update((m) => ({ ...m, [key]: v }));
  }

  protected setCurrents(ev: Event): void {
    this.exportCurrents.set((ev.target as HTMLInputElement).value);
  }

  protected async buildExport(): Promise<void> {
    const r = this.sweep();
    const designs: object[] = [];
    const add = (p: DesignPoint | null | undefined, name: string) => {
      if (p) {
        designs.push({ name, a_hot_mm: p.a_hot_mm, a_cold_mm: p.a_cold_mm, height_mm: p.height_mm, theta_deg: p.theta_deg });
      }
    };
    add(r?.reference, 'reference');
    add(r?.optimum, 'optimum');
    if (!designs.length) {
      const d = this.ws.design();
      if (d) {
        designs.push({ name: d.name, a_hot_mm: d.p_leg.a_hot_mm, a_cold_mm: d.p_leg.a_cold_mm, height_mm: d.height_mm, theta_deg: 0 });
      }
    }
    try {
      this.exportResult.set(await this.api.ansysExport(designs, parseNumberList(this.exportCurrents()), this.paramMap()));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    }
  }

  protected downloadCsv(): void {
    const e = this.exportResult();
    if (e) {
      download('teglab_design_points.csv', e.csv, 'text/csv');
    }
  }

  protected downloadJournal(): void {
    const e = this.exportResult();
    if (e) {
      download('teglab_design_points.wbjn', e.journal, 'text/x-python');
    }
  }

  protected rows(): DesignPoint[] {
    return this.sweep()?.series.flatMap((s) => s.points) ?? [];
  }

  protected isOptimum(p: DesignPoint): boolean {
    const o = this.sweep()?.optimum;
    return !!o && o.x === p.x && o.height_mm === p.height_mm;
  }

  protected value(p: DesignPoint): number | null {
    return num(p[this.obj().field]);
  }
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function round4(v: number): number {
  return Math.round(v * 1e4) / 1e4;
}
