import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { NumPipe, PctPipe, sig } from '../../core/format';
import { PipelineApiService } from '../../core/pipeline-api.service';
import { NODAL_LABEL, NODAL_SYMBOL, levelBadge, verdictBadge } from '../../core/pipeline-labels';
import { PipelineStateService } from '../../core/pipeline-state.service';
import { AXIS_VALUES, Axis, BodyInfo, LayerRow, StackLayer } from '../../core/pipeline.models';
import { ThemeService } from '../../core/theme.service';
import { NumField, StatTile } from '../../shared/fields';
import { PlotComponent, PlotTrace } from '../../shared/plot.component';

const AXIS_INDEX: Record<Axis, number> = { x: 0, y: 1, z: 2 };

/** Mechanical nodal exports: dataset → layer stack → temperature profile and contact conductances. */
@Component({
  selector: 'app-nodal-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NumField, NumPipe, PctPipe, PlotComponent, StatTile],
  templateUrl: './nodal-panel.component.html',
  styleUrl: './pipeline.shared.css',
})
export class NodalPanelComponent {
  protected readonly state = inject(PipelineStateService);
  protected readonly api = inject(PipelineApiService);
  private readonly theme = inject(ThemeService);
  protected readonly dragging = signal(false);
  protected readonly axes = AXIS_VALUES;
  protected readonly nodalLabel = NODAL_LABEL;
  protected readonly levelBadge = levelBadge;
  protected readonly verdictBadge = verdictBadge;

  protected readonly nodalExamples = computed(() => this.state.examples().filter((e) => e.kind === 'nodal'));

  protected readonly totalNodes = computed(() => (this.state.dataset()?.bodies ?? []).reduce((n, b) => n + b.n_nodes, 0));

  protected readonly datasetIssues = computed(() => (this.state.dataset()?.issues ?? []).filter((i) => i.level !== 'info'));

  /** The evaluated profile belongs to another stack than the one being edited. */
  protected readonly stale = computed(() => {
    const prof = this.state.layerProfile();
    const st = this.state.stack();
    if (!prof || !st) {
      return false;
    }
    const used = { axis: st.axis, layers: st.layers.filter((l) => l.bodies.length > 0) };
    return JSON.stringify(used) !== JSON.stringify(prof.stack);
  });

  protected readonly profileTraces = computed<PlotTrace[]>(() => {
    const rows = this.state.layerProfile()?.rows.filter((r) => r.kind === 'layer') ?? [];
    const pts = rows.flatMap((r) => [
      { s: r.s_hot_mm, t: r.t_hot_c, label: `${r.name} · hot face` },
      { s: r.s_cold_mm, t: r.t_cold_c, label: `${r.name} · cold face` },
    ]);
    return [
      {
        name: 'face-averaged temperature',
        x: pts.map((p) => p.s),
        y: pts.map((p) => p.t),
        text: pts.map((p) => p.label),
        mode: 'lines+markers',
        slot: 0,
        markerSize: 8,
      },
    ];
  });

  protected readonly profileShapes = computed(() => {
    const rows = this.state.layerProfile()?.rows ?? [];
    const ink = this.theme.ink();
    return rows
      .filter((r) => r.kind === 'layer' && r.active)
      .map((r) => ({
        type: 'rect',
        xref: 'x',
        yref: 'paper',
        x0: r.s_hot_mm,
        x1: r.s_cold_mm,
        y0: 0,
        y1: 1,
        fillcolor: ink.series[0],
        opacity: 0.08,
        line: { width: 0 },
        layer: 'below',
      }));
  });

  /** Direct labels on the thermoelectric contact jumps (selective: only jumps worth reading). */
  protected readonly profileAnnotations = computed(() => {
    const rows = this.state.layerProfile()?.rows ?? [];
    const ink = this.theme.ink();
    const out: object[] = [];
    rows.forEach((r, i) => {
      const before = rows[i - 1];
      const after = rows[i + 1];
      if (r.kind !== 'interface' || !before || !after || !(before.active || after.active) || Math.abs(r.dt_k) < 0.01) {
        return;
      }
      const y = ((before.t_cold_c ?? 0) + (after.t_hot_c ?? 0)) / 2;
      out.push({
        x: r.s_hot_mm,
        y,
        text: `contact ΔT ${sig(r.dt_k, 3)} K`,
        showarrow: true,
        arrowcolor: ink.muted,
        arrowwidth: 1,
        ax: before.active ? -70 : 70,
        ay: 0,
        font: { color: ink.secondary, size: 11 },
        bgcolor: ink.surface,
      });
    });
    return out;
  });

  protected readonly layerRows = computed<LayerRow[]>(() => this.state.layerProfile()?.rows ?? []);

  // ---------------------------------------------------------------- files
  protected onFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.length) {
      void this.state.uploadNodal(files);
    }
  }

  protected onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
    const files = Array.from(ev.dataTransfer?.files ?? []);
    if (files.length) {
      void this.state.uploadNodal(files);
    }
  }

  protected onDragOver(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(true);
  }

  // ---------------------------------------------------------------- stack editing
  private edit(fn: (layers: StackLayer[]) => StackLayer[]): void {
    const st = this.state.stack();
    if (st) {
      this.state.stack.set({ ...st, layers: fn(st.layers.map((l) => ({ ...l, bodies: [...l.bodies] }))) });
    }
  }

  protected layerOf(tag: string): number {
    return this.state.stack()?.layers.findIndex((l) => l.bodies.includes(tag)) ?? -1;
  }

  protected assign(tag: string, ev: Event): void {
    const target = Number((ev.target as HTMLSelectElement).value);
    this.edit((layers) =>
      layers.map((l, k) => {
        const bodies = l.bodies.filter((b) => b !== tag);
        return { ...l, bodies: k === target ? [...bodies, tag].sort() : bodies };
      }),
    );
  }

  protected rename(k: number, ev: Event): void {
    const name = (ev.target as HTMLInputElement).value.trim();
    if (name) {
      this.edit((layers) => layers.map((l, i) => (i === k ? { ...l, name } : l)));
    }
  }

  protected setActive(k: number): void {
    this.edit((layers) => layers.map((l, i) => ({ ...l, active: i === k })));
  }

  protected move(k: number, step: -1 | 1): void {
    this.edit((layers) => {
      const j = k + step;
      if (j < 0 || j >= layers.length) {
        return layers;
      }
      [layers[k], layers[j]] = [layers[j], layers[k]];
      return layers;
    });
  }

  protected reverse(): void {
    this.edit((layers) => layers.reverse());
  }

  protected addLayer(): void {
    this.edit((layers) => [...layers, { name: `layer ${layers.length + 1}`, bodies: [], active: false }]);
  }

  protected removeLayer(k: number): void {
    this.edit((layers) => layers.filter((l, i) => i !== k || l.bodies.length > 0));
  }

  protected setAxis(ev: Event): void {
    const axis = (ev.target as HTMLSelectElement).value as Axis;
    const st = this.state.stack();
    if (st) {
      this.state.stack.set({ ...st, axis });
    }
  }

  protected setTolUm(value: number): void {
    if (value > 0) {
      this.state.tolMm.set(value / 1000);
    }
  }

  protected setExpected(key: 'tcc_w_m2k' | 'ecc_s_m2', value: number): void {
    if (value > 0) {
      this.state.expected.update((e) => ({ ...e, [key]: value }));
    }
  }

  protected symbols(b: BodyInfo): string {
    return b.quantities.map((q) => NODAL_SYMBOL[q]).join(' · ');
  }

  protected quantityNames(b: BodyInfo): string {
    return b.quantities.map((q) => NODAL_LABEL[q]).join(', ');
  }

  protected extent(b: BodyInfo): string {
    const axis = this.state.stack()?.axis ?? 'y';
    const i = AXIS_INDEX[axis];
    return `${sig(b.min_mm[i], 4)} … ${sig(b.max_mm[i], 4)}`;
  }

  protected share(row: LayerRow): number | null {
    const total = this.state.layerProfile()?.split?.dt_total_k;
    return total ? row.dt_k / total : null;
  }
}
