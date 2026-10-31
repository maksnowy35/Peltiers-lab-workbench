import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { NumPipe, PctPipe } from '../../core/format';
import { PipelineApiService } from '../../core/pipeline-api.service';
import { Badge, levelBadge } from '../../core/pipeline-labels';
import { BcMode, PipelineStateService } from '../../core/pipeline-state.service';
import { Issue, ResistanceBreakdown, SystemReport } from '../../core/pipeline.models';
import { ThemeService } from '../../core/theme.service';
import { NumField } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

interface IssueRow {
  where: string;
  issue: Issue;
}

const CONTACT_BADGE: Record<ResistanceBreakdown['contact_verdict'], Badge> = {
  active: { kind: 'ok', icon: '✓', text: 'Active' },
  inactive: { kind: 'error', icon: '×', text: 'Inactive' },
  inconclusive: { kind: 'warn', icon: '!', text: 'Inconclusive' },
  unknown: { kind: 'info', icon: '-', text: 'Unknown' },
};

/** Workbench parameter tables: inspect → run settings → per-system physics and the variant comparison. */
@Component({
  selector: 'app-parameters-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NumField, NumPipe, PctPipe, PlotComponent],
  templateUrl: './parameters-panel.component.html',
  styleUrl: './pipeline.shared.css',
})
export class ParametersPanelComponent {
  protected readonly state = inject(PipelineStateService);
  protected readonly api = inject(PipelineApiService);
  private readonly theme = inject(ThemeService);
  protected readonly dragging = signal(false);
  protected readonly showInfo = signal(false);
  protected readonly levelBadge = levelBadge;

  protected readonly examples = computed(() => this.state.examples().filter((e) => e.kind === 'parameters'));

  protected readonly unresolved = computed(() => {
    const names = (this.state.inspection()?.parameters ?? []).filter((p) => p.rule === 'unresolved').map((p) => p.name);
    return [...new Set(names)];
  });

  protected readonly aliasHint = computed(
    () => '"aliases": { "<canonical key>": [' + this.unresolved().map((n) => JSON.stringify(n)).join(', ') + '] }',
  );

  protected readonly issues = computed<IssueRow[]>(() => {
    const rep = this.state.report();
    if (!rep) {
      return [];
    }
    const rows: IssueRow[] = rep.issues.map((issue) => ({ where: issue.system ?? issue.source ?? 'project', issue }));
    for (const s of rep.systems) {
      for (const issue of s.issues) {
        if (!rep.issues.includes(issue)) {
          rows.push({ where: s.system, issue });
        }
      }
    }
    const seen = new Set<string>();
    const unique = rows.filter((r) => {
      const key = `${r.where}|${r.issue.code}|${r.issue.message}`;
      return seen.has(key) ? false : (seen.add(key), true);
    });
    const order = { error: 0, warning: 1, info: 2 } as const;
    return unique.sort((a, b) => order[a.issue.level] - order[b.issue.level]);
  });

  protected readonly shownIssues = computed(() =>
    this.showInfo() ? this.issues() : this.issues().filter((r) => r.issue.level !== 'info'),
  );

  protected readonly infoCount = computed(() => this.issues().filter((r) => r.issue.level === 'info').length);

  protected readonly contactTraces = computed<PlotTrace[]>(() => {
    const systems = this.state.report()?.systems ?? [];
    const x = systems.map((s) => this.systemLabel(s));
    return [
      { name: 'isolated from the data', x, y: systems.map((s) => s.resistance.contacts_isolated_ohm), type: 'bar', slot: 0 },
      { name: 'expected from ECC', x, y: systems.map((s) => s.resistance.contacts_expected_ohm), type: 'bar', slot: 1 },
    ];
  });

  protected readonly comparisonTraces = computed<PlotTrace[]>(() => {
    const rows = this.state.report()?.comparison ?? [];
    const x = rows.map((c) => `${c.variant} (${c.system})`);
    return [
      { name: 'K', x, y: rows.map((c) => c.k_ratio), type: 'bar', slot: 0 },
      { name: 'R', x, y: rows.map((c) => c.r_ratio), type: 'bar', slot: 1 },
      { name: 'P frozen α²Q²/(4K²R)', x, y: rows.map((c) => c.p_frozen_ratio), type: 'bar', slot: 2 },
      { name: 'P exact BC-QT', x, y: rows.map((c) => c.p_exact_ratio), type: 'bar', slot: 3 },
      { name: 'P at BC-TT', x, y: rows.map((c) => c.p_tt_ratio), type: 'bar', slot: 4 },
    ];
  });

  protected readonly unityLine = computed(() => [
    {
      type: 'line',
      xref: 'paper',
      x0: 0,
      x1: 1,
      y0: 1,
      y1: 1,
      line: { color: this.theme.ink().muted, width: 1.5, dash: 'dot' },
    },
  ]);

  protected readonly referenceName = computed(
    () => this.state.reference() || this.state.profile()?.reference_variant || 'the reference',
  );

  // ---------------------------------------------------------------- files
  protected onFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.length) {
      void this.state.setParamFiles([...this.state.paramFiles(), ...files]);
    }
  }

  protected onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
    const files = Array.from(ev.dataTransfer?.files ?? []);
    if (files.length) {
      void this.state.setParamFiles([...this.state.paramFiles(), ...files]);
    }
  }

  protected onDragOver(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(true);
  }

  protected removeFile(name: string): void {
    void this.state.setParamFiles(this.state.paramFiles().filter((f) => f.name !== name));
  }

  protected setProfile(ev: Event): void {
    void this.state.selectProfile((ev.target as HTMLSelectElement).value);
  }

  // ---------------------------------------------------------------- settings
  protected setMode(mode: BcMode): void {
    this.state.condition.update((c) => ({ ...c, mode }));
  }

  protected setCondition(key: 'tHotC' | 'qHotW' | 'tColdC' | 'currentA', value: number): void {
    this.state.condition.update((c) => ({ ...c, [key]: value }));
  }

  protected clearCurrent(): void {
    this.state.condition.update((c) => ({ ...c, currentA: null }));
  }

  protected setVariant(letter: string, ev: Event): void {
    const value = (ev.target as HTMLSelectElement).value;
    this.state.variants.update((v) => ({ ...v, [letter]: value }));
  }

  protected setReference(ev: Event): void {
    this.state.reference.set((ev.target as HTMLSelectElement).value);
  }

  // ---------------------------------------------------------------- report helpers
  protected systemLabel(s: SystemReport): string {
    return `${s.system} · ${s.variant ?? '?'}`;
  }

  protected contactBadge(s: SystemReport): Badge {
    return CONTACT_BADGE[s.resistance.contact_verdict];
  }
}
