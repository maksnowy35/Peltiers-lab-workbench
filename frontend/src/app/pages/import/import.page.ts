import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { NumPipe } from '../../core/format';
import { CaseMeta, CaseSummary, ExampleFile, ParsedColumn, ParsedTable, RoleInfo } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { NumField } from '../../shared/fields';

type Contacts = 'default' | 'finite' | 'ideal';

interface CaseDef {
  index: number;
  name: string;
  bc: 'TT' | 'QT' | 'other';
  q_hot_w: number | null;
  t_hot_c: number | null;
  t_cold_c: number | null;
  variant: string;
  a_hot_mm: number | null;
  a_cold_mm: number | null;
  height_mm: number | null;
  contacts: Contacts;
}

interface CasePreview {
  index: number;
  name: string;
  roles: string[];
  points: number;
}

const OUTPUT_ROLES = new Set(['voltage', 'power', 'q_hot', 'q_cold', 't_hot_ext', 't_cold_ext', 't_leg_hot', 't_leg_cold',
  't_max', 't_avg', 'j_avg', 'j_max', 'joule_avg', 'joule_max', 'v_avg', 't_profile']);

@Component({
  selector: 'app-import',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, NumField, NumPipe],
  templateUrl: './import.page.html',
  styleUrl: './import.page.css',
})
export class ImportPage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly files = signal<File[]>([]);
  protected readonly pasteText = signal('');
  protected readonly examples = signal<ExampleFile[]>([]);
  protected readonly roles = signal<RoleInfo[]>([]);
  protected readonly table = signal<ParsedTable | null>(null);
  protected readonly caseDefs = signal<CaseDef[]>([]);
  protected readonly splitBy = signal<string | null>(null);
  protected readonly timeToCurrent = signal<number | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly created = signal<{ cases: CaseSummary[]; notes: string[] } | null>(null);
  protected readonly dragging = signal(false);

  protected readonly variantNames = computed(() => Object.keys(this.ws.reference()?.variants ?? {}));

  protected readonly caseOptions = computed(() => this.caseDefs().map((c) => c.index));

  protected readonly previews = computed<CasePreview[]>(() => {
    const t = this.table();
    if (!t) {
      return [];
    }
    return this.caseDefs().map((c) => {
      const cols = t.columns.filter((col) => col.numeric && (col.case === 0 || col.case === c.index) &&
        col.role !== 'ignore' && col.role !== 'label');
      const roles = [...new Set(cols.map((col) => col.role))];
      const current = cols.find((col) => col.role === 'current');
      const outputs = cols.filter((col) => OUTPUT_ROLES.has(col.role));
      let points = 0;
      for (let r = 0; r < t.n_rows; r++) {
        const hasOut = outputs.some((col) => col.values[r] !== null);
        const hasCur = !current || current.values[r] !== null;
        if (hasOut && hasCur) {
          points++;
        }
      }
      return { index: c.index, name: c.name, roles, points };
    });
  });

  constructor() {
    void this.api.examples().then((e) => this.examples.set(e), () => this.examples.set([]));
    void this.api.roles().then((r) => this.roles.set(r), () => this.roles.set([]));
  }

  // ------------------------------------------------------------------ source
  protected onFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.files.set(Array.from(input.files ?? []));
    input.value = '';
  }

  protected onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
    const list = ev.dataTransfer?.files;
    if (list?.length) {
      this.files.set(Array.from(list));
    }
  }

  protected onDragOver(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(true);
  }

  protected onPaste(ev: Event): void {
    this.pasteText.set((ev.target as HTMLTextAreaElement).value);
  }

  protected clearFiles(): void {
    this.files.set([]);
  }

  protected async loadExample(name: string): Promise<void> {
    try {
      const text = await this.api.exampleText(name);
      this.files.set([new File([text], name, { type: 'text/csv' })]);
      this.pasteText.set('');
      await this.parse();
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    }
  }

  protected async parse(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.created.set(null);
    try {
      const t = await this.api.parse(this.files(), this.pasteText().trim() || null);
      this.table.set(t);
      this.splitBy.set(null);
      this.timeToCurrent.set(t.columns.some((c) => c.role === 'time') && !t.columns.some((c) => c.role === 'current') ? 1 : null);
      this.caseDefs.set(
        Array.from({ length: t.n_cases }, (_, k) => this.defaultCase(k + 1, t.case_names[k] ?? `Case ${k + 1}`, t.suggested_meta[k] ?? {})),
      );
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(false);
    }
  }

  private defaultCase(index: number, name: string, meta: Partial<CaseMeta>): CaseDef {
    const bc = meta.bc ?? 'QT';
    return {
      index,
      name,
      bc,
      q_hot_w: meta.q_hot_w ?? (bc === 'QT' ? 0.3152 : null),
      t_hot_c: meta.t_hot_c ?? (bc === 'TT' ? 100 : null),
      t_cold_c: meta.t_cold_c ?? 20,
      variant: '',
      a_hot_mm: null,
      a_cold_mm: null,
      height_mm: null,
      contacts: 'default',
    };
  }

  // ------------------------------------------------------------------ mapping
  protected setColumn(key: string, patch: Partial<ParsedColumn>): void {
    const t = this.table();
    if (!t) {
      return;
    }
    this.table.set({ ...t, columns: t.columns.map((c) => (c.key === key ? { ...c, ...patch } : c)) });
  }

  protected setRole(col: ParsedColumn, ev: Event): void {
    const role = (ev.target as HTMLSelectElement).value;
    this.setColumn(col.key, { role, confidence: 1, case: role === 'current' || role === 'time' ? 0 : col.case || 1 });
  }

  protected setCaseIndex(col: ParsedColumn, ev: Event): void {
    this.setColumn(col.key, { case: Number((ev.target as HTMLSelectElement).value) });
  }

  protected setUnit(col: ParsedColumn, ev: Event): void {
    const unit = (ev.target as HTMLInputElement).value.trim();
    this.setColumn(col.key, { unit: unit || null });
  }

  protected setSplit(ev: Event): void {
    const v = (ev.target as HTMLSelectElement).value;
    this.splitBy.set(v || null);
  }

  protected sample(col: ParsedColumn): string {
    const vals = col.numeric ? col.values.filter((v): v is number => v !== null).slice(0, 4).map((v) => formatSample(v)) : col.text.slice(0, 3);
    return vals.join(', ');
  }

  protected confidenceClass(c: number): string {
    return c >= 0.8 ? 'hi' : c >= 0.5 ? 'mid' : 'lo';
  }

  // ------------------------------------------------------------------ cases
  protected patchCase(index: number, patch: Partial<CaseDef>): void {
    this.caseDefs.update((defs) => defs.map((c) => (c.index === index ? { ...c, ...patch } : c)));
  }

  protected setCaseName(index: number, ev: Event): void {
    this.patchCase(index, { name: (ev.target as HTMLInputElement).value });
  }

  protected setCaseBc(index: number, ev: Event): void {
    const bc = (ev.target as HTMLSelectElement).value as CaseDef['bc'];
    this.patchCase(index, { bc });
  }

  protected setContacts(index: number, ev: Event): void {
    this.patchCase(index, { contacts: (ev.target as HTMLSelectElement).value as Contacts });
  }

  protected setVariant(index: number, ev: Event): void {
    const name = (ev.target as HTMLSelectElement).value;
    const v = this.ws.reference()?.variants[name];
    if (v) {
      this.patchCase(index, { variant: name, a_hot_mm: +v.a_hot_mm.toFixed(4), a_cold_mm: v.a_cold_mm, height_mm: v.height_mm });
    } else {
      this.patchCase(index, { variant: name });
    }
  }

  protected addCase(): void {
    const defs = this.caseDefs();
    if (defs.length >= 8) {
      return;
    }
    const idx = Math.max(0, ...defs.map((d) => d.index)) + 1;
    this.caseDefs.set([...defs, this.defaultCase(idx, `Case ${idx}`, {})]);
  }

  protected removeCase(index: number): void {
    if (this.caseDefs().length <= 1) {
      return;
    }
    this.caseDefs.update((defs) => defs.filter((d) => d.index !== index));
    const t = this.table();
    if (t) {
      this.table.set({ ...t, columns: t.columns.map((c) => (c.case === index ? { ...c, case: 0, role: 'ignore' } : c)) });
    }
  }

  protected async commit(): Promise<void> {
    const t = this.table();
    if (!t) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const cases = this.caseDefs().map((c) => ({
        index: c.index,
        name: c.name.trim() || `Case ${c.index}`,
        meta: {
          bc: c.bc,
          q_hot_w: c.bc === 'QT' ? c.q_hot_w : null,
          t_hot_c: c.bc === 'TT' ? c.t_hot_c : null,
          t_cold_c: c.t_cold_c,
          variant: c.variant || null,
          a_hot_mm: c.a_hot_mm,
          a_cold_mm: c.a_cold_mm,
          height_mm: c.height_mm,
          contacts_active: c.contacts === 'default' ? null : c.contacts === 'finite',
        } satisfies Partial<CaseMeta>,
      }));
      const res = await this.api.commit({ table: t, cases, split_by: this.splitBy(), time_to_current: this.timeToCurrent() });
      this.created.set(res);
      await this.ws.refreshCases();
      this.ws.selectedCaseIds.set(res.cases.map((c) => c.id));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    } finally {
      this.busy.set(false);
    }
  }

  protected async deleteCase(c: CaseSummary): Promise<void> {
    await this.api.deleteCase(c.id);
    await this.ws.refreshCases();
  }

  protected async reseed(): Promise<void> {
    await this.api.reseed();
    await this.ws.refreshCases();
  }

  protected roleLabel(key: string): string {
    return this.roles().find((r) => r.key === key)?.label ?? key;
  }

  protected metaSummary(c: CaseSummary): string {
    const m = c.meta;
    const bc = m.bc === 'QT' ? `QT ${m.q_hot_w ?? '?'} W` : m.bc === 'TT' ? `TT ${m.t_hot_c ?? '?'} °C` : 'other';
    const geo = m.variant ?? (m.a_hot_mm !== null ? `${m.a_hot_mm}/${m.a_cold_mm} mm` : '');
    return [bc, geo].filter(Boolean).join(' · ');
  }
}

function formatSample(v: number): string {
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) {
    return v.toExponential(2);
  }
  return String(Number(v.toPrecision(5)));
}
