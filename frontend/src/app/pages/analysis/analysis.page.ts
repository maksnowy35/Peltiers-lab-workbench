import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { NumPipe, PctPipe } from '../../core/format';
import { AnalysisResponse, CaseAnalysis, DenseCurve, GciResult, ModelComparison, PairDesign, Series } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { ChecksList, NumField } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

const MAX_CASES = 8;

type SeriesKey = keyof CaseAnalysis['points'];

@Component({
  selector: 'app-analysis',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, PlotComponent, ChecksList, NumField, NumPipe, PctPipe],
  templateUrl: './analysis.page.html',
  styleUrl: './analysis.page.css',
})
export class AnalysisPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly result = signal<AnalysisResponse | null>(null);
  protected readonly model = signal<ModelComparison[]>([]);
  protected readonly overlay = signal(true);
  protected readonly referenceId = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly openChecks = signal<string | null>(null);

  // GCI calculator (pre-filled with thesis Section 3.5.3: dT_TE on meshes C1/C2)
  protected readonly gciCoarse = signal(76.159);
  protected readonly gciFine = signal(76.187);
  protected readonly gciMedium = signal<number | null>(null);
  protected readonly gciR = signal(1.99);
  protected readonly gciP = signal(2);
  protected readonly gciResult = signal<GciResult | null>(null);

  private runId = 0;

  protected readonly selected = computed(() => {
    const ids = this.ws.selectedCaseIds();
    return this.ws.cases().filter((c) => ids.includes(c.id)).slice(0, MAX_CASES);
  });

  protected readonly tooMany = computed(() => this.ws.selectedCaseIds().length > MAX_CASES);

  protected readonly warnCount = computed(() => {
    const r = this.result();
    return r ? r.analyses.reduce((n, a) => n + a.checks.filter((c) => c.level === 'warn' || c.level === 'error').length, 0) : 0;
  });

  protected readonly uTraces = computed(() => this.traces('voltage_v', 'voltage_v'));
  protected readonly pTraces = computed(() => [...this.traces('power_w', 'power_w'), ...this.optimumTraces()]);
  protected readonly etaTraces = computed(() => this.traces('efficiency', 'efficiency'));
  protected readonly thTraces = computed(() => this.traces('t_hot_ext_c', 't_hot_ext_c'));
  protected readonly dtTraces = computed(() => this.traces('dt_te_k', 'dt_te_k'));
  protected readonly qcTraces = computed(() => this.traces('q_cold_w', 'q_cold_w'));

  constructor() {
    effect(() => {
      const ids = this.selected().map((c) => c.id);
      const design = this.ws.design();
      const ref = this.referenceId();
      const overlay = this.overlay();
      untracked(() => void this.run(ids, design, ref, overlay));
    });
    void this.computeGci();
  }

  private async run(ids: string[], design: PairDesign | null, ref: string | null, overlay: boolean): Promise<void> {
    const id = ++this.runId;
    if (!ids.length) {
      this.result.set(null);
      this.model.set([]);
      return;
    }
    this.busy.set(true);
    try {
      const [res, cmp] = await Promise.all([
        this.api.analysis(ids, design, ref),
        overlay && design ? this.api.compare(design, ids, 41) : Promise.resolve([] as ModelComparison[]),
      ]);
      if (id !== this.runId) {
        return;
      }
      this.result.set(res);
      this.model.set(cmp);
      this.error.set(null);
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

  protected slotOf(caseId: string): number {
    const k = this.ws.cases().findIndex((c) => c.id === caseId);
    return k < 0 ? 0 : k % MAX_CASES;
  }

  private traces(key: SeriesKey, denseKey: Exclude<keyof DenseCurve, 'current_a'> | null): PlotTrace[] {
    const res = this.result();
    if (!res) {
      return [];
    }
    const out: PlotTrace[] = [];
    for (const a of res.analyses) {
      const y: Series = a.points[key];
      if (!y.some((v) => v !== null)) {
        continue;
      }
      const slot = this.slotOf(a.case_id);
      const m = this.model().find((c) => c.case_id === a.case_id);
      if (m?.dense && denseKey) {
        out.push({ name: `${a.name} · model`, x: m.dense.current_a, y: m.dense[denseKey], mode: 'lines', slot, showInLegend: false });
      }
      out.push({ name: a.name, x: a.points.current_a, y, mode: 'markers', slot });
    }
    return out;
  }

  private optimumTraces(): PlotTrace[] {
    const res = this.result();
    if (!res) {
      return [];
    }
    return res.analyses
      .filter((a) => a.curve.best && a.curve.best.method !== 'largest sampled point')
      .map((a) => ({
        name: `${a.name} · P_max`,
        x: [a.curve.best?.i_opt_a ?? null],
        y: [a.curve.best?.p_max_w ?? null],
        mode: 'markers' as const,
        slot: this.slotOf(a.case_id),
        hollow: true,
        markerSize: 14,
        showInLegend: false,
        text: [a.curve.best?.method ?? ''],
      }));
  }

  protected toggle(id: string): void {
    this.ws.toggleCase(id);
  }

  protected setReference(ev: Event): void {
    const v = (ev.target as HTMLSelectElement).value;
    this.referenceId.set(v || null);
  }

  protected toggleOverlay(ev: Event): void {
    this.overlay.set((ev.target as HTMLInputElement).checked);
  }

  protected selectAll(): void {
    this.ws.selectedCaseIds.set(this.ws.cases().slice(0, MAX_CASES).map((c) => c.id));
  }

  protected selectNone(): void {
    this.ws.selectedCaseIds.set([]);
  }

  protected countWarnings(a: CaseAnalysis): number {
    return a.checks.filter((c) => c.level === 'warn' || c.level === 'error').length;
  }

  protected modelErrors(caseId: string): { key: string; rms: number }[] {
    const m = this.model().find((c) => c.case_id === caseId);
    if (!m) {
      return [];
    }
    return Object.entries(m.errors).map(([key, e]) => ({ key, rms: e.rms_rel }));
  }

  protected async computeGci(): Promise<void> {
    try {
      this.gciResult.set(
        await this.api.gci({
          f_coarse: this.gciCoarse(),
          f_fine: this.gciFine(),
          r: this.gciR(),
          p: this.gciP(),
          fs: null,
          f_medium: this.gciMedium(),
        }),
      );
    } catch (err) {
      this.gciResult.set(null);
      this.error.set(err instanceof Error ? err.message : String(err));
    }
  }

  protected setGciMedium(v: number): void {
    this.gciMedium.set(v);
  }

  protected clearGciMedium(): void {
    this.gciMedium.set(null);
    void this.computeGci();
  }
}
