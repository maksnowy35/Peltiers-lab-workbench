import { Injectable, computed, inject, signal } from '@angular/core';

import { ApiService } from './api.service';
import { CaseSummary, MaterialInfo, PairDesign, ReferenceData } from './models';

const DESIGN_KEY = 'teglab.design.v1';
const CALIB_KEY = 'teglab.calibration.v1';

function readStorage(key: string): string | null {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) {
      globalThis.localStorage?.removeItem(key);
    } else {
      globalThis.localStorage?.setItem(key, value);
    }
  } catch {
    /* storage unavailable (private mode) - the app keeps working in memory */
  }
}

export function cloneDesign(d: PairDesign): PairDesign {
  return structuredClone(d);
}

/**
 * Shared state of the workbench: the active pair design (edited on the Model page and
 * used by Optimizer/Lab), imported cases and the material library.
 */
@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  private readonly api = inject(ApiService);

  readonly reference = signal<ReferenceData | null>(null);
  readonly design = signal<PairDesign | null>(null);
  readonly calibrationNote = signal<string | null>(readStorage(CALIB_KEY));
  readonly cases = signal<CaseSummary[]>([]);
  readonly materials = signal<MaterialInfo[]>([]);
  readonly selectedCaseIds = signal<string[]>([]);
  readonly error = signal<string | null>(null);
  readonly ready = signal(false);

  readonly materialNames = computed(() => {
    const map = new Map<string, string>();
    for (const m of this.materials()) {
      map.set(m.id, m.name);
    }
    return map;
  });

  readonly designLabel = computed(() => {
    const d = this.design();
    if (!d) {
      return '';
    }
    const b = d.boundary;
    const hot = b.hot_mode === 'T' ? `T_h ${b.t_hot_c} °C` : b.hot_mode === 'Q' ? `Q_h ${b.q_hot_w} W` : `source ${b.t_source_c} °C`;
    const cold = b.cold_mode === 'T' ? `T_c ${b.t_cold_c} °C` : `sink ${b.t_sink_c} °C`;
    const theta = Math.atan((d.p_leg.a_cold_mm - d.p_leg.a_hot_mm) / (2 * d.height_mm)) * (180 / Math.PI);
    return `${d.name} · θ ${theta.toFixed(1)}° · ${hot} / ${cold}`;
  });

  private initPromise: Promise<void> | null = null;

  init(): Promise<void> {
    this.initPromise ??= this.load();
    return this.initPromise;
  }

  private async load(): Promise<void> {
    try {
      const [ref, cases, materials] = await Promise.all([this.api.reference(), this.api.cases(), this.api.materials()]);
      this.reference.set(ref);
      this.cases.set(cases);
      this.materials.set(materials);
      const stored = readStorage(DESIGN_KEY);
      let design: PairDesign = ref.design;
      if (stored) {
        try {
          design = JSON.parse(stored) as PairDesign;
        } catch {
          design = ref.design;
        }
      }
      this.design.set(design);
      if (!this.selectedCaseIds().length) {
        this.selectedCaseIds.set(cases.slice(0, 4).map((c) => c.id));
      }
      this.error.set(null);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
      this.initPromise = null;
    } finally {
      this.ready.set(true);
    }
  }

  setDesign(d: PairDesign, calibrationNote?: string | null): void {
    this.design.set(cloneDesign(d));
    writeStorage(DESIGN_KEY, JSON.stringify(d));
    if (calibrationNote !== undefined) {
      this.calibrationNote.set(calibrationNote);
      writeStorage(CALIB_KEY, calibrationNote);
    }
  }

  resetDesign(): void {
    const ref = this.reference();
    if (ref) {
      this.setDesign(ref.design, 'Reference design (calibrated on thesis A0-TT)');
    }
  }

  async refreshCases(): Promise<void> {
    const cases = await this.api.cases();
    this.cases.set(cases);
    const ids = new Set(cases.map((c) => c.id));
    this.selectedCaseIds.update((sel) => sel.filter((id) => ids.has(id)));
  }

  async refreshMaterials(): Promise<void> {
    this.materials.set(await this.api.materials());
  }

  toggleCase(id: string): void {
    this.selectedCaseIds.update((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]));
  }
}
