import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterRenderEffect,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';

import { NumPipe, download } from '../core/format';
import { PipelineApiService } from '../core/pipeline-api.service';
import { NODAL_LABEL, VIEW_LABEL } from '../core/pipeline-labels';
import {
  ImageCalibration,
  NodalDatasetInfo,
  NodalKey,
  NodalPoints,
  OverlayPlacement,
  VIEW_VALUES,
  View,
} from '../core/pipeline.models';
import { ChartInk, ThemeService } from '../core/theme.service';
import { NumField } from './fields';
import { loadPlotly } from './plot.component';

interface ImageSource {
  key: string;
  label: string;
  url: string;
  width: number;
  height: number;
  calibration: ImageCalibration | null;
  calibrationSource: 'exact' | 'camera' | 'none';
  view: View | null;
  quantity: NodalKey | null;
}

type CalMode = 'fit' | 'manual';
type Pair = [number, number];

interface RenderSpec {
  points: NodalPoints;
  placement: OverlayPlacement | null;
  image: ImageSource | null;
  opacity: number;
  size: number;
  show: boolean;
  ink: ChartInk;
}

/**
 * Nodal samples of a dataset drawn over a contour screenshot. The nodes come from the backend already
 * projected onto a standard view; the picture is placed with a two-point calibration whose placement is
 * computed by `POST /api/pipeline/overlay`. Values always come from the nodes - the picture is a backdrop.
 */
@Component({
  selector: 'app-overlay-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NumField, NumPipe],
  templateUrl: './overlay-viewer.component.html',
  styleUrl: './overlay-viewer.component.css',
})
export class OverlayViewerComponent {
  readonly dataset = input.required<NodalDatasetInfo>();

  private readonly api = inject(PipelineApiService);
  private readonly theme = inject(ThemeService);
  private readonly plotHost = viewChild<ElementRef<HTMLDivElement>>('plot');
  private plotted = false;
  private resizeObs: ResizeObserver | null = null;
  private pointsRequest = 0;
  private placementRequest = 0;

  protected readonly views = VIEW_VALUES;
  protected readonly viewLabel = VIEW_LABEL;
  protected readonly nodalLabel = NODAL_LABEL;

  // ---------------------------------------------------------------- data layer
  protected readonly view = signal<View>('front');
  protected readonly quantity = signal<NodalKey>('temperature_c');
  protected readonly surfaceOnly = signal(true);
  protected readonly maxPoints = signal(20000);
  protected readonly points = signal<NodalPoints | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  // ---------------------------------------------------------------- picture layer
  protected readonly upload = signal<ImageSource | null>(null);
  protected readonly imageKey = signal('');
  protected readonly images = computed<ImageSource[]>(() => {
    const fromExport = this.dataset().images.map<ImageSource>((img) => ({
      key: `dataset:${img.name}`,
      label: img.name,
      url: img.url,
      width: img.width_px,
      height: img.height_px,
      calibration: img.calibration,
      calibrationSource: img.calibration_source,
      view: img.view,
      quantity: img.quantity,
    }));
    const up = this.upload();
    return up ? [...fromExport, up] : fromExport;
  });
  protected readonly image = computed(() => this.images().find((i) => i.key === this.imageKey()) ?? null);
  protected readonly calibration = signal<ImageCalibration | null>(null);
  protected readonly calibrationNote = signal('');
  protected readonly placement = signal<OverlayPlacement | null>(null);
  protected readonly calibrating = signal(false);
  protected readonly mode = signal<CalMode>('fit');
  protected readonly picks = signal<Pair[]>([]);
  protected readonly manualModel = signal<[Pair, Pair]>([
    [0, 0],
    [1, 1],
  ]);
  protected readonly opacity = signal(0.85);
  protected readonly markerSize = signal(5);
  protected readonly showPoints = signal(true);

  constructor() {
    const destroyRef = inject(DestroyRef);

    effect(() => {
      const ds = this.dataset();
      untracked(() => this.reset(ds));
    });

    effect(() => {
      const req = {
        id: this.dataset().dataset_id,
        view: this.view(),
        quantity: this.quantity(),
        surface_only: this.surfaceOnly(),
        max_points: this.maxPoints(),
      };
      untracked(() => void this.loadPoints(req.id, req.view, req.quantity, req.surface_only, req.max_points));
    });

    effect(() => {
      const cal = this.calibration();
      untracked(() => void this.place(cal));
    });

    afterRenderEffect(() => {
      const el = this.plotHost()?.nativeElement;
      const points = this.points();
      if (!el || !points) {
        return;
      }
      const spec: RenderSpec = {
        points,
        placement: this.placement(),
        image: this.image(),
        opacity: this.opacity(),
        size: this.markerSize(),
        show: this.showPoints(),
        ink: this.theme.ink(),
      };
      void this.render(el, spec);
    });

    destroyRef.onDestroy(() => {
      this.resizeObs?.disconnect();
      const el = this.plotHost()?.nativeElement;
      if (this.plotted && el) {
        void loadPlotly().then((p) => p.purge(el));
      }
    });
  }

  // ---------------------------------------------------------------- controls
  protected setView(ev: Event): void {
    this.view.set((ev.target as HTMLSelectElement).value as View);
    this.followPicture();
  }

  protected setQuantity(ev: Event): void {
    this.quantity.set((ev.target as HTMLSelectElement).value as NodalKey);
    this.followPicture();
  }

  protected setFlag(target: 'surface' | 'points', ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    (target === 'surface' ? this.surfaceOnly : this.showPoints).set(on);
  }

  protected setSlider(target: 'opacity' | 'size', ev: Event): void {
    const v = (ev.target as HTMLInputElement).valueAsNumber;
    (target === 'opacity' ? this.opacity : this.markerSize).set(v);
  }

  protected onSelectImage(ev: Event): void {
    this.selectImage((ev.target as HTMLSelectElement).value, true);
  }

  /**
   * Show `key`; `adopt` = also switch to the picture's view and quantity (an explicit choice of the user).
   */
  protected selectImage(key: string, adopt: boolean): void {
    this.imageKey.set(key);
    this.picks.set([]);
    const img = this.image();
    if (!img) {
      this.calibration.set(null);
      this.calibrating.set(false);
      return;
    }
    if (adopt && img.view) {
      this.view.set(img.view);
    }
    if (adopt && img.quantity && this.dataset().quantities.includes(img.quantity)) {
      this.quantity.set(img.quantity);
    }
    this.calibration.set(img.calibration);
    this.calibrationNote.set(
      img.calibrationSource === 'exact'
        ? 'exact (synthetic picture)'
        : img.calibrationSource === 'camera'
          ? 'from the export camera - check that the outlines line up'
          : 'not calibrated',
    );
    this.calibrating.set(!img.calibration);
  }

  /**
   * Keep a picture from the export in step with the view and quantity: the exact (view, quantity) match,
   * else any picture of this view, else none - a front-view picture must not sit under top-view nodes.
   * An uploaded picture is the user's choice and stays.
   */
  private followPicture(): void {
    const current = this.image();
    if (current?.key === 'upload') {
      return;
    }
    const sameView = this.images().filter((i) => i.key !== 'upload' && (i.view === null || i.view === this.view()));
    const best =
      sameView.find((i) => i.quantity === this.quantity()) ?? (current && sameView.includes(current) ? current : sameView[0]);
    const key = best?.key ?? '';
    if (key !== this.imageKey()) {
      this.selectImage(key, false);
    }
  }

  protected onUpload(ev: Event): void {
    const inputEl = ev.target as HTMLInputElement;
    const file = inputEl.files?.[0];
    inputEl.value = '';
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      const probe = new Image();
      probe.onload = () => {
        this.upload.set({
          key: 'upload',
          label: file.name,
          url,
          width: probe.naturalWidth,
          height: probe.naturalHeight,
          calibration: null,
          calibrationSource: 'none',
          view: null,
          quantity: null,
        });
        this.selectImage('upload', true);
      };
      probe.onerror = () => this.error.set(`${file.name}: not an image the browser can read`);
      probe.src = url;
    };
    reader.readAsDataURL(file);
  }

  /** The plotted nodes as CSV - the table view of this chart (thousands of rows do not fit a page). */
  protected downloadCsv(): void {
    const p = this.points();
    if (!p) {
      return;
    }
    const rows = [`body,${p.h_label},${p.v_label},${NODAL_LABEL[p.quantity]} [${p.unit}]`];
    for (let k = 0; k < p.value.length; k++) {
      rows.push(`${p.bodies[p.body_index[k]] ?? ''},${p.h_mm[k]},${p.v_mm[k]},${p.value[k]}`);
    }
    download(`teglab-${p.view}-${p.quantity}.csv`, rows.join('\n'), 'text/csv');
  }

  // ---------------------------------------------------------------- calibration
  protected setMode(mode: CalMode): void {
    this.mode.set(mode);
    this.picks.set([]);
  }

  protected setManual(point: 0 | 1, axis: 0 | 1, value: number): void {
    const next = this.manualModel().map((p) => [...p]) as [Pair, Pair];
    next[point][axis] = value;
    this.manualModel.set(next);
    if (this.picks().length === 2) {
      this.applyPicks();
    }
  }

  protected pick(ev: MouseEvent): void {
    const img = ev.currentTarget as HTMLImageElement;
    const rect = img.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * img.naturalWidth;
    const py = ((ev.clientY - rect.top) / rect.height) * img.naturalHeight;
    const next: Pair[] = this.picks().length >= 2 ? [[px, py]] : [...this.picks(), [px, py]];
    this.picks.set(next);
    if (next.length === 2) {
      this.applyPicks();
    }
  }

  protected pickStyle(p: Pair): { left: string; top: string } {
    const img = this.image();
    return img ? { left: `${(p[0] / img.width) * 100}%`, top: `${(p[1] / img.height) * 100}%` } : { left: '0', top: '0' };
  }

  private applyPicks(): void {
    const [a, b] = this.picks();
    const img = this.image();
    const pts = this.points();
    if (!a || !b || !img) {
      return;
    }
    if (Math.abs(a[0] - b[0]) < 3 || Math.abs(a[1] - b[1]) < 3) {
      this.error.set('Pick two points that differ clearly in both directions (e.g. opposite corners).');
      this.picks.set([]);
      return;
    }
    this.error.set(null);
    if (this.mode() === 'fit') {
      if (!pts) {
        return;
      }
      // opposite corners in any order: left/right ↔ h range, bottom/top ↔ v range (pixel rows grow down)
      this.calibration.set({
        width_px: img.width,
        height_px: img.height,
        p1_px: [Math.min(a[0], b[0]), Math.max(a[1], b[1])],
        p1_model: [pts.h_range_mm[0], pts.v_range_mm[0]],
        p2_px: [Math.max(a[0], b[0]), Math.min(a[1], b[1])],
        p2_model: [pts.h_range_mm[1], pts.v_range_mm[1]],
      });
      this.calibrationNote.set('two clicks on the part corners');
    } else {
      const [m1, m2] = this.manualModel();
      this.calibration.set({ width_px: img.width, height_px: img.height, p1_px: a, p1_model: m1, p2_px: b, p2_model: m2 });
      this.calibrationNote.set('two known points');
    }
  }

  // ---------------------------------------------------------------- server calls
  private reset(ds: NodalDatasetInfo): void {
    this.upload.set(null);
    this.points.set(null);
    this.placement.set(null);
    this.error.set(null);
    this.quantity.set(ds.quantities.includes('temperature_c') ? 'temperature_c' : (ds.quantities[0] ?? 'temperature_c'));
    this.selectImage(ds.images[0] ? `dataset:${ds.images[0].name}` : '', true);
  }

  private async loadPoints(id: string, view: View, quantity: NodalKey, surfaceOnly: boolean, maxPoints: number) {
    const token = ++this.pointsRequest;
    this.loading.set(true);
    try {
      const pts = await this.api.points(id, { view, quantity, surface_only: surfaceOnly, max_points: maxPoints });
      if (token === this.pointsRequest) {
        this.points.set(pts);
        this.error.set(null);
        const [h, v] = [pts.h_range_mm, pts.v_range_mm];
        if (!this.calibration()) {
          this.manualModel.set([
            [h[0], v[0]],
            [h[1], v[1]],
          ]);
        }
      }
    } catch (err) {
      if (token === this.pointsRequest) {
        this.points.set(null);
        this.error.set(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (token === this.pointsRequest) {
        this.loading.set(false);
      }
    }
  }

  private async place(cal: ImageCalibration | null): Promise<void> {
    const token = ++this.placementRequest;
    if (!cal) {
      this.placement.set(null);
      return;
    }
    try {
      const placement = await this.api.overlay(cal);
      if (token === this.placementRequest) {
        this.placement.set(placement);
      }
    } catch (err) {
      if (token === this.placementRequest) {
        this.placement.set(null);
        this.error.set(err instanceof Error ? err.message : String(err));
      }
    }
  }

  // ---------------------------------------------------------------- Plotly
  private async render(el: HTMLDivElement, s: RenderSpec): Promise<void> {
    const Plotly = await loadPlotly();
    const p = s.points;
    const ink = s.ink;
    const ramp = ink.sequential;
    const name = NODAL_LABEL[p.quantity];
    const narrow = el.clientWidth < 520; // phones: the colorbar goes below the plot to leave the width to the model
    const colorbar = {
      title: { text: `${name} [${p.unit}]`, side: narrow ? 'top' : 'right', font: { color: ink.secondary, size: 12 } },
      tickfont: { color: ink.muted, size: 11 },
      outlinewidth: 0,
      thickness: narrow ? 10 : 12,
      ...(narrow ? { orientation: 'h', x: 0.5, xanchor: 'center', y: -0.2, yanchor: 'top', len: 0.95 } : {}),
    };
    const data: object[] = s.show
      ? [
          {
            type: 'scattergl',
            mode: 'markers',
            x: p.h_mm,
            y: p.v_mm,
            customdata: p.value.map((v, k) => [p.bodies[p.body_index[k]] ?? '', v]),
            marker: {
              color: p.value,
              colorscale: ramp.map((c, i) => [i / (ramp.length - 1), c]),
              cmin: p.value_range[0],
              cmax: p.value_range[1],
              size: s.size,
              line: { color: ink.surface, width: 0.5 },
              colorbar,
            },
            hovertemplate:
              `%{customdata[0]}<br>${p.h_label}: %{x:.4f}<br>${p.v_label}: %{y:.4f}` +
              `<br><b>%{customdata[1]:.6g} ${p.unit}</b><extra></extra>`,
          },
        ]
      : [];
    let [x0, x1] = p.h_range_mm;
    let [y0, y1] = p.v_range_mm;
    const pl = s.placement;
    const images: object[] = [];
    if (pl && s.image) {
      x0 = Math.min(x0, pl.x);
      x1 = Math.max(x1, pl.x + pl.sizex);
      y0 = Math.min(y0, pl.y - pl.sizey);
      y1 = Math.max(y1, pl.y);
      images.push({ source: s.image.url, xref: 'x', yref: 'y', ...pl, opacity: s.opacity });
    }
    const pad = 0.03 * Math.max(x1 - x0, y1 - y0, 1e-9);
    const axis = (title: string, range: number[]) => ({
      title: { text: title, font: { color: ink.secondary, size: 12 }, standoff: 8 },
      range,
      gridcolor: ink.grid,
      linecolor: ink.axis,
      zeroline: false,
      showline: true,
      tickfont: { color: ink.muted, size: 11 },
      automargin: true,
    });
    const layout = {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 12, color: ink.secondary },
      margin: { l: narrow ? 44 : 56, r: narrow ? 8 : 16, t: 10, b: narrow ? 96 : 44 },
      xaxis: { ...axis(p.h_label, [x0 - pad, x1 + pad]), constrain: 'domain' },
      yaxis: { ...axis(p.v_label, [y0 - pad, y1 + pad]), scaleanchor: 'x', scaleratio: 1 },
      images,
      hovermode: 'closest',
      hoverlabel: { bgcolor: ink.surface, bordercolor: ink.axis, font: { color: ink.text, size: 12 } },
      showlegend: false,
    };
    await Plotly.react(el, data, layout, {
      displaylogo: false,
      responsive: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
      toImageButtonOptions: { format: 'png', scale: 2, filename: 'teglab-overlay' },
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
