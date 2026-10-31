import { Injectable, computed, inject, signal } from '@angular/core';

import { PipelineApiService } from './pipeline-api.service';
import {
  CaseConditionInput,
  ContactProfile,
  ExampleInfo,
  LayerProfile,
  LayerStack,
  NodalDatasetInfo,
  PipelineReport,
  ProfileRequest,
  ProjectProfile,
  RunSettings,
  SystemRef,
} from './pipeline.models';

export type BcMode = 'profile' | 'TT' | 'QT';

export interface ConditionDraft {
  mode: BcMode;
  tHotC: number;
  qHotW: number;
  tColdC: number;
  /** operating current of single-point outline exports (they carry no current column) */
  currentA: number | null;
}

/**
 * State of the ANSYS pipeline page, kept in a root service so it survives navigation: uploaded files,
 * reports, the nodal dataset (which lives on the server under `dataset_id`) and the edited layer stack.
 */
@Injectable({ providedIn: 'root' })
export class PipelineStateService {
  private readonly api = inject(PipelineApiService);
  private initialized = false;

  readonly profiles = signal<string[]>([]);
  readonly profileName = signal('thesis-2026');
  readonly profile = signal<ProjectProfile | null>(null);
  readonly examples = signal<ExampleInfo[]>([]);

  // ------------------------------------------------------------------ parameter tables
  readonly paramFiles = signal<File[]>([]);
  readonly inspection = signal<PipelineReport | null>(null);
  readonly report = signal<PipelineReport | null>(null);
  readonly condition = signal<ConditionDraft>({ mode: 'profile', tHotC: 100, qHotW: 0.3152, tColdC: 20, currentA: null });
  readonly variants = signal<Record<string, string>>({});
  readonly reference = signal('');
  readonly paramBusy = signal(false);
  readonly paramError = signal<string | null>(null);

  // ------------------------------------------------------------------ nodal datasets
  readonly dataset = signal<NodalDatasetInfo | null>(null);
  readonly stack = signal<LayerStack | null>(null);
  readonly layerProfile = signal<LayerProfile | null>(null);
  readonly expected = signal<ContactProfile>({ tcc_w_m2k: 1e5, ecc_s_m2: 1e9 });
  readonly tolMm = signal(0.002);
  readonly nodalBusy = signal(false);
  readonly nodalError = signal<string | null>(null);

  /** Systems found by the inspection, with titles where the export carries them. */
  readonly systems = computed<SystemRef[]>(() => {
    const report = this.inspection();
    if (!report) {
      return [];
    }
    const titled = new Map(report.sources.flatMap((s) => s.systems).map((s) => [s.letter, s] as const));
    const letters = [...new Set(report.parameters.map((p) => p.system).filter((l): l is string => !!l))].sort();
    return letters.map((letter) => titled.get(letter) ?? { letter, title: '', cell: null });
  });

  readonly variantOptions = computed<string[]>(() => {
    const p = this.profile();
    const names = new Set([...(p?.systems.map((r) => r.variant) ?? []), p?.reference_variant ?? 'A0']);
    return [...names].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  });

  readonly runSettings = computed<RunSettings>(() => {
    const c = this.condition();
    const base = this.profile()?.default_condition ?? null;
    let condition: CaseConditionInput | null = null;
    if (c.mode === 'TT') {
      condition = { bc: 'TT', t_hot_face_c: c.tHotC, t_cold_face_c: c.tColdC, current_a: c.currentA };
    } else if (c.mode === 'QT') {
      condition = { bc: 'QT', q_hot_w: c.qHotW, t_cold_face_c: c.tColdC, current_a: c.currentA };
    } else if (c.currentA !== null && base) {
      condition = { ...base, current_a: c.currentA };
    }
    const variants = Object.fromEntries(Object.entries(this.variants()).filter(([, v]) => v !== ''));
    return {
      profile: this.profileName(),
      default_condition: condition,
      variants,
      reference_variant: this.reference() || null,
    };
  });

  init(): void {
    if (this.initialized) {
      return;
    }
    this.initialized = true;
    void this.api.profiles().then((p) => this.profiles.set(p), () => this.profiles.set([]));
    void this.api.examples().then((e) => this.examples.set(e), () => this.examples.set([]));
    void this.selectProfile(this.profileName());
  }

  async selectProfile(name: string): Promise<void> {
    this.profileName.set(name);
    try {
      const p = await this.api.profile(name);
      this.profile.set(p);
      this.expected.set(p.contacts);
    } catch (err) {
      this.paramError.set(message(err));
    }
    if (this.paramFiles().length) {
      await this.inspect();
    }
  }

  // ------------------------------------------------------------------ parameter tables
  async setParamFiles(files: File[]): Promise<void> {
    this.paramFiles.set(files);
    this.report.set(null);
    this.variants.set({});
    if (files.length) {
      await this.inspect();
    } else {
      this.inspection.set(null);
    }
  }

  async addExample(example: ExampleInfo): Promise<void> {
    try {
      const file = await this.api.exampleFile(example);
      if (example.kind === 'nodal') {
        await this.uploadNodal([file]);
      } else {
        await this.setParamFiles([...this.paramFiles().filter((f) => f.name !== file.name), file]);
      }
    } catch (err) {
      (example.kind === 'nodal' ? this.nodalError : this.paramError).set(message(err));
    }
  }

  async inspect(): Promise<void> {
    await this.paramTask(async () => this.inspection.set(await this.api.inspect(this.paramFiles(), this.profileName())));
  }

  async run(): Promise<void> {
    await this.paramTask(async () => this.report.set(await this.api.run(this.paramFiles(), this.runSettings())));
  }

  private async paramTask(task: () => Promise<void>): Promise<void> {
    this.paramBusy.set(true);
    this.paramError.set(null);
    try {
      await task();
    } catch (err) {
      this.paramError.set(message(err));
    } finally {
      this.paramBusy.set(false);
    }
  }

  // ------------------------------------------------------------------ nodal datasets
  async uploadNodal(files: File[]): Promise<void> {
    await this.nodalTask(async () => this.setDataset(await this.api.uploadNodal(files)));
  }

  async demo(contacts: 'applied' | 'bonded'): Promise<void> {
    await this.nodalTask(async () => this.setDataset(await this.api.demoNodal(contacts)));
  }

  resetStack(): void {
    this.stack.set(this.dataset()?.proposed_stack ?? null);
    this.layerProfile.set(null);
  }

  async evaluate(): Promise<void> {
    const ds = this.dataset();
    const stack = this.stack();
    if (!ds || !stack) {
      return;
    }
    const req: ProfileRequest = {
      stack: { axis: stack.axis, layers: stack.layers.filter((l) => l.bodies.length > 0) },
      tol_mm: this.tolMm(),
      contacts: this.expected(),
      profile: this.profileName(),
    };
    await this.nodalTask(async () => this.layerProfile.set(await this.api.layerProfile(ds.dataset_id, req)));
  }

  async closeDataset(): Promise<void> {
    const ds = this.dataset();
    this.dataset.set(null);
    this.stack.set(null);
    this.layerProfile.set(null);
    if (ds) {
      await this.api.deleteNodal(ds.dataset_id).catch(() => undefined);
    }
  }

  private setDataset(info: NodalDatasetInfo): void {
    const previous = this.dataset();
    if (previous && previous.dataset_id !== info.dataset_id) {
      void this.api.deleteNodal(previous.dataset_id).catch(() => undefined);
    }
    this.dataset.set(info);
    this.stack.set(info.proposed_stack);
    this.layerProfile.set(null);
  }

  private async nodalTask(task: () => Promise<void>): Promise<void> {
    this.nodalBusy.set(true);
    this.nodalError.set(null);
    try {
      await task();
    } catch (err) {
      const text = message(err);
      this.nodalError.set(text);
      if (text.includes('dataset not found')) {
        this.dataset.set(null); // expired on the server (or the server restarted)
        this.stack.set(null);
        this.layerProfile.set(null);
      }
    } finally {
      this.nodalBusy.set(false);
    }
  }
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
