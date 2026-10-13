import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { aHotFromAngle, NumPipe, thetaDeg } from '../core/format';
import { Boundary, ColdMode, Contacts, HotMode, MaterialInfo, PairDesign, Passive } from '../core/models';
import { cloneDesign } from '../core/workspace.service';
import { NumField } from './fields';

type TopKey = 'height_mm' | 'c3d' | 'alpha_factor' | 'side_loss_h' | 't_ambient_c' | 'n_elements';
type Leg = 'p_leg' | 'n_leg';

export const THESIS_VARIANTS: { name: string; theta: number }[] = [
  { name: 'A0', theta: 0 },
  { name: 'A5', theta: 5 },
  { name: 'A10', theta: 10 },
  { name: 'A15', theta: 15 },
];

/** Form for every parameter of the 1D pair model. Emits a new design object on each change. */
@Component({
  selector: 'app-design-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NumField, NumPipe],
  templateUrl: './design-editor.component.html',
  styles: `
    .variants { display: flex; gap: 6px; flex-wrap: wrap; }
    .seg-row { display: grid; grid-template-columns: minmax(0, 1fr) 90px 28px; gap: 6px; align-items: end; }
    .seg-row select { width: 100%; }
    .leg-title { display: flex; align-items: center; justify-content: space-between; margin: 8px 0 4px; }
    .derived { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .block + .block { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 12px; }
    .name-input { width: 100%; }
  `,
})
export class DesignEditorComponent {
  readonly design = input.required<PairDesign>();
  readonly materials = input<MaterialInfo[]>([]);
  readonly compact = input(false);
  readonly designChange = output<PairDesign>();

  readonly variants = THESIS_VARIANTS;
  readonly pMaterials = computed(() => this.materials().filter((m) => m.kind === 'p'));
  readonly nMaterials = computed(() => this.materials().filter((m) => m.kind === 'n'));

  readonly theta = computed(() => {
    const d = this.design();
    return thetaDeg(d.p_leg.a_hot_mm, d.p_leg.a_cold_mm, d.height_mm);
  });

  readonly areaRatio = computed(() => {
    const d = this.design();
    return (d.n_leg.a_cold_mm / d.p_leg.a_cold_mm) ** 2;
  });

  readonly volumes = computed(() => {
    const d = this.design();
    const v = (a: number, c: number) => (d.height_mm / 3) * (a * a + a * c + c * c);
    const vp = v(d.p_leg.a_hot_mm, d.p_leg.a_cold_mm);
    const vn = v(d.n_leg.a_hot_mm, d.n_leg.a_cold_mm);
    return { p: vp, n: vn, total: vp + vn };
  });

  private emit(mutate: (d: PairDesign) => void): void {
    const d = cloneDesign(this.design());
    mutate(d);
    this.designChange.emit(d);
  }

  setName(ev: Event): void {
    const name = (ev.target as HTMLInputElement).value.trim();
    this.emit((d) => (d.name = name || 'Untitled design'));
  }

  setTop(key: TopKey, value: number): void {
    this.emit((d) => {
      d[key] = key === 'n_elements' ? Math.round(value) : value;
    });
  }

  setHeight(value: number): void {
    if (value <= 0) {
      return;
    }
    const theta = this.theta();
    this.emit((d) => {
      d.height_mm = value;
      this.applyTaper(d, d.p_leg.a_cold_mm, theta);
    });
  }

  setTaper(theta: number): void {
    this.emit((d) => this.applyTaper(d, d.p_leg.a_cold_mm, theta));
  }

  setColdBase(value: number): void {
    const theta = this.theta();
    this.emit((d) => this.applyTaper(d, value, theta));
  }

  setAreaRatio(value: number): void {
    if (value <= 0) {
      return;
    }
    this.emit((d) => {
      const s = Math.sqrt(value);
      d.n_leg.a_hot_mm = round4(d.p_leg.a_hot_mm * s);
      d.n_leg.a_cold_mm = round4(d.p_leg.a_cold_mm * s);
    });
  }

  applyVariant(theta: number): void {
    this.emit((d) => {
      d.height_mm = 1.4;
      this.applyTaper(d, 1.4, theta);
      d.name = `A${theta} (thesis geometry)`;
    });
  }

  private applyTaper(d: PairDesign, aCold: number, theta: number): void {
    const ratio = (d.n_leg.a_cold_mm / d.p_leg.a_cold_mm) ** 2;
    const s = Math.sqrt(Number.isFinite(ratio) && ratio > 0 ? ratio : 1);
    const aHot = aHotFromAngle(aCold, d.height_mm, theta);
    if (aHot <= 0.01) {
      return;
    }
    d.p_leg.a_cold_mm = round4(aCold);
    d.p_leg.a_hot_mm = round4(aHot);
    d.n_leg.a_cold_mm = round4(aCold * s);
    d.n_leg.a_hot_mm = round4(aHot * s);
  }

  setLegSide(leg: Leg, key: 'a_hot_mm' | 'a_cold_mm', value: number): void {
    if (value <= 0) {
      return;
    }
    this.emit((d) => (d[leg][key] = value));
  }

  setSegmentMaterial(leg: Leg, index: number, ev: Event): void {
    const id = (ev.target as HTMLSelectElement).value;
    this.emit((d) => (d[leg].segments[index].material = id));
  }

  setSegmentFraction(leg: Leg, index: number, value: number): void {
    this.emit((d) => {
      const segs = d[leg].segments;
      if (segs.length < 2) {
        return;
      }
      const v = Math.min(Math.max(value, 0.02), 0.98);
      segs[index].fraction = v;
      const rest = segs.filter((_, k) => k !== index);
      const restTotal = rest.reduce((a, s) => a + s.fraction, 0) || 1;
      for (const s of rest) {
        s.fraction = ((1 - v) * s.fraction) / restTotal;
      }
    });
  }

  addSegment(leg: Leg): void {
    this.emit((d) => {
      const segs = d[leg].segments;
      if (segs.length >= 3) {
        return;
      }
      const f = 1 / (segs.length + 1);
      for (const s of segs) {
        s.fraction *= 1 - f;
      }
      segs.unshift({ material: segs[0].material, fraction: f });
    });
  }

  removeSegment(leg: Leg, index: number): void {
    this.emit((d) => {
      const segs = d[leg].segments;
      if (segs.length <= 1) {
        return;
      }
      segs.splice(index, 1);
      const total = segs.reduce((a, s) => a + s.fraction, 0) || 1;
      for (const s of segs) {
        s.fraction /= total;
      }
    });
  }

  setContacts<K extends keyof Contacts>(key: K, value: Contacts[K]): void {
    this.emit((d) => (d.contacts[key] = value));
  }

  toggleContacts(ev: Event): void {
    this.setContacts('active', (ev.target as HTMLInputElement).checked);
  }

  setPassive<K extends keyof Passive>(key: K, value: Passive[K]): void {
    this.emit((d) => (d.passive[key] = value));
  }

  setBoundary<K extends keyof Boundary>(key: K, value: Boundary[K]): void {
    this.emit((d) => (d.boundary[key] = value));
  }

  setHotMode(mode: HotMode): void {
    this.setBoundary('hot_mode', mode);
  }

  setColdMode(mode: ColdMode): void {
    this.setBoundary('cold_mode', mode);
  }

  materialName(id: string): string {
    return this.materials().find((m) => m.id === id)?.name ?? id;
  }
}

function round4(v: number): number {
  return Math.round(v * 1e4) / 1e4;
}
