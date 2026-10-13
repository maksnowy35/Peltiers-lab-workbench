import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterRenderEffect,
  computed,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';

import { sig } from '../core/format';
import { ChartInk, ThemeService } from '../core/theme.service';

type PlotlyApi = typeof import('plotly.js-dist-min').default;

let plotlyLoader: Promise<PlotlyApi> | null = null;

/** plotly.js is ~4.5 MB, so it is loaded lazily the first time a chart is shown. */
function loadPlotly(): Promise<PlotlyApi> {
  plotlyLoader ??= import('plotly.js-dist-min').then((m) => m.default);
  return plotlyLoader;
}

export interface PlotTrace {
  name?: string;
  x: (number | null)[];
  y: (number | null)[];
  mode?: 'lines' | 'markers' | 'lines+markers';
  /** categorical palette slot (0-based, fixed order - color follows the entity) */
  slot?: number;
  /** use the muted ink instead of a series color (reference / context lines) */
  muted?: boolean;
  /** hollow markers, e.g. infeasible designs */
  hollow?: boolean;
  type?: 'scatter' | 'bar';
  text?: string[];
  showInLegend?: boolean;
  markerSize?: number;
  /** marks the model curve vs data points in tooltips */
  hoverSuffix?: string;
}

export interface HeatmapSpec {
  x: number[];
  y: number[];
  z: (number | null)[][];
  zTitle: string;
  zScale?: number;
}

interface TableView {
  columns: string[];
  rows: string[][];
}

@Component({
  selector: 'app-plot',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <figure class="plot">
      <figcaption class="plot-head">
        <div>
          @if (title()) {
            <h3>{{ title() }}</h3>
          }
          @if (subtitle()) {
            <p class="sub">{{ subtitle() }}</p>
          }
        </div>
        @if (hasData()) {
          <button type="button" class="btn ghost small" (click)="showTable.set(!showTable())"
                  [attr.aria-pressed]="showTable()">
            {{ showTable() ? 'Chart' : 'Table' }}
          </button>
        }
      </figcaption>
      <div #host class="plot-host" [style.height.px]="height()" [class.hidden]="showTable() || !hasData()"></div>
      @if (!hasData()) {
        <div class="empty" [style.height.px]="height()">{{ emptyText() }}</div>
      }
      @if (showTable() && hasData()) {
        <div class="table-wrap plot-table" [style.max-height.px]="height() + 40">
          <table class="data">
            <thead>
              <tr>
                @for (c of table().columns; track $index) {
                  <th>{{ c }}</th>
                }
              </tr>
            </thead>
            <tbody>
              @for (r of table().rows; track $index) {
                <tr>
                  @for (v of r; track $index) {
                    <td>{{ v }}</td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </figure>
  `,
  styles: `
    :host { display: block; min-width: 0; }
    .plot { margin: 0; }
    .plot-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; min-height: 26px; }
    .plot-head h3 { font-size: 13px; }
    .plot-head .sub { color: var(--muted); font-size: 12px; }
    .plot-host { width: 100%; }
    .plot-host.hidden { display: none; }
    .plot-table { overflow: auto; margin-top: 6px; }
    .empty { display: grid; place-items: center; border: 1px dashed var(--line); border-radius: 6px; }
  `,
})
export class PlotComponent {
  readonly traces = input<PlotTrace[]>([]);
  readonly heatmap = input<HeatmapSpec | null>(null);
  readonly title = input('');
  readonly subtitle = input('');
  readonly xTitle = input('');
  readonly yTitle = input('');
  readonly height = input(280);
  readonly xScale = input(1);
  readonly yScale = input(1);
  readonly shapes = input<object[]>([]);
  readonly annotations = input<object[]>([]);
  readonly hoverMode = input<'x unified' | 'closest'>('x unified');
  readonly emptyText = input('No data yet');
  readonly yZero = input(false);
  /** set false when a shared legend (e.g. the case list of the page) identifies the series */
  readonly legend = input(true);
  /** shallow-merged into layout.xaxis / layout.yaxis (e.g. custom tick labels) */
  readonly layoutPatch = input<{ xaxis?: object; yaxis?: object }>({});

  readonly showTable = signal(false);
  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private readonly theme = inject(ThemeService);
  private plotted = false;
  private resizeObs: ResizeObserver | null = null;

  readonly hasData = computed(() => {
    const hm = this.heatmap();
    if (hm) {
      return hm.z.some((row) => row.some((v) => v !== null));
    }
    return this.traces().some((t) => t.y.some((v) => v !== null && Number.isFinite(v)));
  });

  readonly table = computed<TableView>(() => {
    const hm = this.heatmap();
    const xs = this.xScale();
    const ys = this.yScale();
    if (hm) {
      const rows: string[][] = [];
      hm.y.forEach((yv, i) =>
        hm.x.forEach((xv, j) => rows.push([sig(yv, 4), sig(xv, 4), sig(hm.z[i]?.[j] ?? null, 4, hm.zScale ?? 1)])),
      );
      return { columns: [this.yTitle() || 'y', this.xTitle() || 'x', hm.zTitle], rows: rows.slice(0, 600) };
    }
    const traces = this.traces().filter((t) => t.y.length);
    const first = traces[0]?.x ?? [];
    const shared = traces.every((t) => t.x.length === first.length && t.x.every((v, k) => v === first[k]));
    if (shared && traces.length) {
      const rows = first.map((xv, k) => [sig(xv, 5, xs), ...traces.map((t) => sig(t.y[k] ?? null, 5, ys))]);
      return { columns: [this.xTitle() || 'x', ...traces.map((t, k) => t.name ?? `series ${k + 1}`)], rows };
    }
    const rows: string[][] = [];
    for (const [k, t] of traces.entries()) {
      t.x.forEach((xv, i) => rows.push([t.name ?? `series ${k + 1}`, sig(xv, 5, xs), sig(t.y[i] ?? null, 5, ys)]));
    }
    return { columns: ['series', this.xTitle() || 'x', this.yTitle() || 'y'], rows: rows.slice(0, 600) };
  });

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterRenderEffect(() => {
      const el = this.host().nativeElement;
      const spec = {
        traces: this.traces(),
        heatmap: this.heatmap(),
        xTitle: this.xTitle(),
        yTitle: this.yTitle(),
        xScale: this.xScale(),
        yScale: this.yScale(),
        shapes: this.shapes(),
        annotations: this.annotations(),
        hoverMode: this.hoverMode(),
        yZero: this.yZero(),
        legend: this.legend(),
        layoutPatch: this.layoutPatch(),
        ink: this.theme.ink(),
        visible: this.hasData() && !this.showTable(),
      };
      if (!spec.visible) {
        return;
      }
      void this.render(el, spec);
    });
    destroyRef.onDestroy(() => {
      this.resizeObs?.disconnect();
      if (this.plotted) {
        void loadPlotly().then((p) => p.purge(this.host().nativeElement));
      }
    });
  }

  private async render(
    el: HTMLDivElement,
    s: {
      traces: PlotTrace[];
      heatmap: HeatmapSpec | null;
      xTitle: string;
      yTitle: string;
      xScale: number;
      yScale: number;
      shapes: object[];
      annotations: object[];
      hoverMode: 'x unified' | 'closest';
      yZero: boolean;
      legend: boolean;
      layoutPatch: { xaxis?: object; yaxis?: object };
      ink: ChartInk;
    },
  ): Promise<void> {
    const Plotly = await loadPlotly();
    const ink = s.ink;
    const data: object[] = [];
    if (s.heatmap) {
      const hm = s.heatmap;
      const scale = hm.zScale ?? 1;
      data.push({
        type: 'heatmap',
        x: hm.x,
        y: hm.y,
        z: hm.z.map((row) => row.map((v) => (v === null ? null : v * scale))),
        colorscale: ink.sequential.map((c, i) => [i / (ink.sequential.length - 1), c]),
        hoverongaps: false,
        xgap: 2,
        ygap: 2,
        colorbar: {
          title: { text: hm.zTitle, side: 'right', font: { color: ink.secondary, size: 12 } },
          tickfont: { color: ink.muted },
          outlinewidth: 0,
          thickness: 12,
        },
        hovertemplate: `${s.xTitle}: %{x}<br>${s.yTitle}: %{y}<br><b>%{z:.4g}</b> ${hm.zTitle}<extra></extra>`,
      });
    }
    const named = s.legend ? s.traces.filter((t) => t.name && t.showInLegend !== false).length : 0;
    for (const t of s.traces) {
      const color = t.muted ? ink.muted : ink.series[(t.slot ?? 0) % ink.series.length];
      const mode = t.mode ?? 'lines';
      const x = t.x.map((v) => (v === null ? null : v * s.xScale));
      const y = t.y.map((v) => (v === null ? null : v * s.yScale));
      if (t.type === 'bar') {
        data.push({
          type: 'bar',
          x,
          y,
          name: t.name,
          marker: { color, cornerradius: 4 },
          textposition: 'none',
          hovertext: t.text,
          showlegend: named >= 2 && t.showInLegend !== false,
          hovertemplate: t.text ? '<b>%{y:.4g}</b> %{hovertext}<extra></extra>' : `<b>%{y:.4g}</b> ${t.name ?? ''}<extra></extra>`,
        });
        continue;
      }
      data.push({
        type: 'scatter',
        mode,
        x,
        y,
        name: t.name,
        text: t.text,
        connectgaps: true,
        line: { color, width: 2, shape: 'linear' },
        marker: {
          color: t.hollow ? ink.surface : color,
          size: t.markerSize ?? 9,
          line: { color: t.hollow ? color : ink.surface, width: 2 },
        },
        showlegend: named >= 2 && t.showInLegend !== false,
        hovertemplate: t.text
          ? `<b>%{y:.4g}</b> ${t.name ?? ''}${t.hoverSuffix ?? ''}<br>%{text}<extra></extra>`
          : `<b>%{y:.4g}</b> ${t.name ?? ''}${t.hoverSuffix ?? ''}<extra></extra>`,
      });
    }
    const axis = (title: string) => ({
      title: { text: title, font: { color: ink.secondary, size: 12 }, standoff: 8 },
      gridcolor: ink.grid,
      linecolor: ink.axis,
      zerolinecolor: ink.axis,
      tickfont: { color: ink.muted, size: 11 },
      showline: true,
      automargin: true,
      separatethousands: true,
    });
    const layout = {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: ink.secondary },
      margin: { l: 56, r: s.heatmap ? 8 : 16, t: named >= 2 ? 34 : 10, b: 44 },
      xaxis: { ...axis(s.xTitle), ...(s.layoutPatch.xaxis ?? {}) },
      yaxis: { ...axis(s.yTitle), rangemode: s.yZero ? 'tozero' : 'normal', ...(s.layoutPatch.yaxis ?? {}) },
      showlegend: named >= 2,
      legend: {
        orientation: 'h',
        x: 0,
        y: 1.02,
        yanchor: 'bottom',
        font: { color: ink.secondary, size: 12 },
        bgcolor: 'rgba(0,0,0,0)',
      },
      hovermode: s.heatmap ? 'closest' : s.hoverMode,
      hoverlabel: { bgcolor: ink.surface, bordercolor: ink.axis, font: { color: ink.text, size: 12 } },
      hoverdistance: 24,
      spikedistance: -1,
      bargap: 0.55,
      barcornerradius: 4,
      shapes: s.shapes,
      annotations: s.annotations,
      colorway: ink.series,
    };
    await Plotly.react(el, data, layout, {
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
      toImageButtonOptions: { format: 'png', scale: 2, filename: 'teglab-chart' },
    });
    this.plotted = true;
    if (!this.resizeObs && typeof ResizeObserver !== 'undefined') {
      this.resizeObs = new ResizeObserver(() => {
        if (el.offsetParent !== null) {
          void Plotly.Plots.resize(el);
        }
      });
      this.resizeObs.observe(el);
    }
  }
}
