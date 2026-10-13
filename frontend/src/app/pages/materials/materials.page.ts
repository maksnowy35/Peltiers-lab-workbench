import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { NumPipe, PctPipe, range } from '../../core/format';
import { EfficiencyMap, MaterialInfo, SegmentedResult, ThomsonResult } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { NumField, StatTile } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

const MAX_SELECTED = 6;

interface PairPreset {
  key: string;
  label: string;
  p: string;
  n: string;
}

const PAIRS: PairPreset[] = [
  { key: 'bite', label: 'Bi2Te3', p: 'bi2te3_p', n: 'bi2te3_n' },
  { key: 'mg', label: 'MgAgSb / Mg3(Sb,Bi)2 (Te-free)', p: 'mgagsb_p', n: 'mg3sbbi_n' },
  { key: 'pbte', label: 'PbTe', p: 'pbte_p', n: 'pbte_n' },
  { key: 'skd', label: 'Skutterudite', p: 'skd_p', n: 'skd_n' },
  { key: 'hh', label: 'Half-Heusler', p: 'hh_p', n: 'hh_n' },
  { key: 'sige', label: 'SiGe', p: 'sige_p', n: 'sige_n' },
];

type CurveKey = 'zt' | 'alpha_uv_k' | 'rho_uohm_m' | 'kappa' | 's_per_v';

@Component({
  selector: 'app-materials',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PlotComponent, NumField, StatTile, NumPipe, PctPipe],
  templateUrl: './materials.page.html',
  styleUrl: './materials.page.css',
})
export class MaterialsPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly pairs = PAIRS;
  /** material id -> palette slot, assigned on selection so colors follow the material */
  protected readonly slots = signal<Record<string, number>>({});
  protected readonly selectedIds = computed(() => Object.keys(this.slots()));

  protected readonly pairKeys = signal<string[]>(['bite', 'mg', 'skd', 'hh']);
  protected readonly tCold = signal(30);
  protected readonly tHotFrom = signal(60);
  protected readonly tHotTo = signal(800);
  protected readonly tHotStep = signal(40);
  protected readonly eff = signal<EfficiencyMap | null>(null);
  protected readonly effBusy = signal(false);

  protected readonly segPHot = signal('skd_p');
  protected readonly segNHot = signal('skd_n');
  protected readonly segPCold = signal('bi2te3_p');
  protected readonly segNCold = signal('bi2te3_n');
  protected readonly segTHot = signal(500);
  protected readonly segTCold = signal(30);
  protected readonly segHeight = signal(5);
  protected readonly seg = signal<SegmentedResult | null>(null);
  protected readonly segBusy = signal(false);

  protected readonly thomson = signal<ThomsonResult | null>(null);
  protected readonly thomsonBusy = signal(false);

  protected readonly uploadName = signal('');
  protected readonly uploadKind = signal<'p' | 'n'>('p');
  protected readonly uploadLimit = signal<number | null>(250);
  protected readonly uploadDensity = signal(7.0);
  protected readonly uploadFile = signal<File | null>(null);
  protected readonly uploadMsg = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);

  protected readonly pMaterials = computed(() => this.ws.materials().filter((m) => m.kind === 'p'));
  protected readonly nMaterials = computed(() => this.ws.materials().filter((m) => m.kind === 'n'));

  protected readonly ztTraces = computed(() => this.curves('zt'));
  protected readonly alphaTraces = computed(() => this.curves('alpha_uv_k'));
  protected readonly rhoTraces = computed(() => this.curves('rho_uohm_m'));
  protected readonly kappaTraces = computed(() => this.curves('kappa'));
  protected readonly sTraces = computed(() => this.curves('s_per_v'));

  protected readonly effTraces = computed<PlotTrace[]>(() => {
    const e = this.eff();
    if (!e) {
      return [];
    }
    const out: PlotTrace[] = [];
    for (const s of e.series) {
      const slot = this.pairSlot(s.p, s.n);
      const limit = Math.min(...[s.p, s.n].map((id) => this.ws.materials().find((m) => m.id === id)?.t_limit_c ?? Infinity));
      const ok = s.points.filter((p) => p.within_limits);
      // "what if" beyond the limit, shown for ~100 K only (properties are clamped outside the data range)
      const bad = s.points.filter((p) => !p.within_limits && p.t_hot_c <= limit + 100);
      out.push({ name: this.pairLabel(s.p, s.n), x: ok.map((p) => p.t_hot_c), y: ok.map((p) => p.eta_max), mode: 'lines', slot });
      if (bad.length) {
        out.push({ name: `${this.pairLabel(s.p, s.n)} (above limit)`, x: bad.map((p) => p.t_hot_c), y: bad.map((p) => p.eta_max),
          mode: 'markers', slot, hollow: true, markerSize: 6, showInLegend: false });
      }
    }
    return out;
  });

  protected readonly densityTraces = computed<PlotTrace[]>(() => {
    const e = this.eff();
    if (!e) {
      return [];
    }
    return e.series.map((s) => {
      const ok = s.points.filter((p) => p.within_limits);
      return { name: this.pairLabel(s.p, s.n), x: ok.map((p) => p.t_hot_c), y: ok.map((p) => p.power_density_w_cm2), slot: this.pairSlot(s.p, s.n) };
    });
  });

  protected readonly segTraces = computed<PlotTrace[]>(() => {
    const r = this.seg();
    if (!r) {
      return [];
    }
    const ok = r.rows.filter((x) => x.feasible);
    const bad = r.rows.filter((x) => !x.feasible);
    return [
      { name: 'within limits', x: ok.map((x) => x.fraction_hot), y: ok.map((x) => x.eta_max), mode: 'lines+markers', slot: 0 },
      { name: 'violates a material limit', x: bad.map((x) => x.fraction_hot), y: bad.map((x) => x.eta_max), mode: 'markers', slot: 0, hollow: true },
    ];
  });

  protected readonly interfaceTraces = computed<PlotTrace[]>(() => {
    const r = this.seg();
    if (!r) {
      return [];
    }
    const rows = r.rows.filter((x) => x.t_interface_c !== null);
    return [{ x: rows.map((x) => x.fraction_hot), y: rows.map((x) => x.t_interface_c), mode: 'lines+markers', slot: 1 }];
  });

  protected readonly interfaceLimit = computed<object[]>(() => {
    const cold = this.ws.materials().find((m) => m.id === this.segPCold());
    return cold
      ? [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: cold.t_limit_c, y1: cold.t_limit_c, line: { color: '#898781', width: 1 } }]
      : [];
  });

  constructor() {
    effect(() => {
      const mats = this.ws.materials();
      if (mats.length && !untracked(() => this.selectedIds().length)) {
        untracked(() => {
          for (const id of ['bi2te3_p', 'mgagsb_p', 'pbte_p', 'skd_p', 'hh_p', 'sige_p']) {
            if (mats.some((m) => m.id === id)) {
              this.toggleMaterial(id);
            }
          }
        });
      }
    });
  }

  // ------------------------------------------------------------------ library
  protected toggleMaterial(id: string): void {
    const cur = { ...this.slots() };
    if (id in cur) {
      delete cur[id];
    } else {
      if (Object.keys(cur).length >= MAX_SELECTED) {
        return;
      }
      const used = new Set(Object.values(cur));
      let slot = 0;
      while (used.has(slot)) {
        slot++;
      }
      cur[id] = slot;
    }
    this.slots.set(cur);
  }

  protected slotOf(id: string): number | null {
    const s = this.slots()[id];
    return s === undefined ? null : s;
  }

  private curves(key: CurveKey): PlotTrace[] {
    const slots = this.slots();
    return this.ws
      .materials()
      .filter((m) => m.id in slots)
      .map((m) => ({ name: m.name, x: m.curve.t_c, y: m.curve[key], slot: slots[m.id] }));
  }

  // ------------------------------------------------------------------ efficiency map
  protected togglePair(key: string): void {
    this.pairKeys.update((k) => (k.includes(key) ? k.filter((x) => x !== key) : [...k, key]));
  }

  private pairSlot(p: string, n: string): number {
    const k = PAIRS.findIndex((x) => x.p === p && x.n === n);
    return k < 0 ? 7 : k;
  }

  private pairLabel(p: string, n: string): string {
    return PAIRS.find((x) => x.p === p && x.n === n)?.label ?? `${p} / ${n}`;
  }

  protected async runEfficiency(): Promise<void> {
    const pairs = PAIRS.filter((p) => this.pairKeys().includes(p.key)).map((p) => [p.p, p.n] as [string, string]);
    if (!pairs.length) {
      return;
    }
    const temps = range(this.tHotFrom(), this.tHotTo(), this.tHotStep()).slice(0, 60);
    this.effBusy.set(true);
    this.error.set(null);
    try {
      this.eff.set(await this.api.efficiencyMap(pairs, this.tCold(), temps));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.effBusy.set(false);
    }
  }

  // ------------------------------------------------------------------ segmentation
  protected setSel(which: 'pHot' | 'nHot' | 'pCold' | 'nCold', ev: Event): void {
    const v = (ev.target as HTMLSelectElement).value;
    ({ pHot: this.segPHot, nHot: this.segNHot, pCold: this.segPCold, nCold: this.segNCold })[which].set(v);
  }

  protected async runSegmented(): Promise<void> {
    this.segBusy.set(true);
    this.error.set(null);
    try {
      this.seg.set(
        await this.api.segmented({
          p_hot: this.segPHot(),
          n_hot: this.segNHot(),
          p_cold: this.segPCold(),
          n_cold: this.segNCold(),
          t_hot_c: this.segTHot(),
          t_cold_c: this.segTCold(),
          fractions: range(0, 1, 0.05),
          height_mm: this.segHeight(),
        }),
      );
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.segBusy.set(false);
    }
  }

  // ------------------------------------------------------------------ Thomson
  protected async runThomson(): Promise<void> {
    const d = this.ws.design();
    if (!d) {
      return;
    }
    this.thomsonBusy.set(true);
    try {
      this.thomson.set(await this.api.thomson(d));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.thomsonBusy.set(false);
    }
  }

  // ------------------------------------------------------------------ upload
  protected onUploadFile(ev: Event): void {
    const f = (ev.target as HTMLInputElement).files?.[0] ?? null;
    this.uploadFile.set(f);
    if (f && !this.uploadName()) {
      this.uploadName.set(f.name.replace(/\.[^.]+$/, ''));
    }
  }

  protected setUploadName(ev: Event): void {
    this.uploadName.set((ev.target as HTMLInputElement).value);
  }

  protected setUploadKind(ev: Event): void {
    this.uploadKind.set((ev.target as HTMLSelectElement).value === 'n' ? 'n' : 'p');
  }

  protected async upload(): Promise<void> {
    const f = this.uploadFile();
    if (!f || !this.uploadName().trim()) {
      return;
    }
    try {
      const m = await this.api.uploadMaterial(f, this.uploadName().trim(), this.uploadKind(), this.uploadLimit(), this.uploadDensity());
      await this.ws.refreshMaterials();
      this.uploadMsg.set(`Added “${m.name}” (${m.t_min_c.toFixed(0)}–${m.t_max_c.toFixed(0)} °C, zT peak ${m.zt_peak.toFixed(2)}).`);
      if (!(m.id in this.slots())) {
        this.toggleMaterial(m.id);
      }
    } catch (err) {
      this.uploadMsg.set(err instanceof Error ? err.message : String(err));
    }
  }

  protected async remove(m: MaterialInfo): Promise<void> {
    await this.api.deleteMaterial(m.id);
    await this.ws.refreshMaterials();
    if (m.id in this.slots()) {
      this.toggleMaterial(m.id);
    }
  }
}
