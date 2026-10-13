import { ChangeDetectionStrategy, Component, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { NumPipe, PctPipe } from '../../core/format';
import { Characterization, Check } from '../../core/models';
import { WorkspaceService } from '../../core/workspace.service';
import { StatTile } from '../../shared/fields';

interface HealthItem {
  caseId: string;
  caseName: string;
  check: Check;
}

@Component({
  selector: 'app-home',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, StatTile, NumPipe, PctPipe],
  templateUrl: './home.page.html',
  styleUrl: './home.page.css',
})
export class HomePage {
  protected readonly ws = inject(WorkspaceService);
  private readonly api = inject(ApiService);

  protected readonly result = signal<Characterization | null>(null);
  protected readonly health = signal<HealthItem[] | null>(null);
  protected readonly error = signal<string | null>(null);

  protected readonly steps = [
    { path: '/import', n: '1', title: 'Import ANSYS results', text: 'Workbench design-point tables, parameter outlines, Mechanical tabular data or lab CSV/XLSX. Repeated outputs of several systems are split into cases automatically.' },
    { path: '/analysis', n: '2', title: 'Analyze load curves', text: 'U(I), P(I), efficiency, heat balance, fitted optimum, variant comparison (Table 5.3 style) and automatic consistency checks, incl. contact resistance.' },
    { path: '/model', n: '3', title: 'Calibrate the 1D model', text: 'Fast thermoelectric model with Peltier, Joule and Thomson terms, contacts and ceramics. Fit its lumped parameters to your ANSYS points.' },
    { path: '/optimizer', n: '4', title: 'Optimize geometry', text: 'Taper angle and height at fixed base or constant volume, under fixed heat flow, fixed temperatures or real heat exchangers – with manufacturing and temperature limits.' },
    { path: '/materials', n: '5', title: 'High-temperature materials', text: 'zT and compatibility curves, efficiency vs hot-side temperature, segmented legs and the Thomson contribution. Upload your own material data.' },
    { path: '/lab', n: '6', title: 'Plan the experiment', text: 'Leg count and material mass, solder paste and reflow, heater and cooling, current set-points, uncertainty budget and a printable run sheet.' },
  ];

  constructor() {
    effect(() => {
      const d = this.ws.design();
      if (d) {
        untracked(() => void this.load());
      }
    });
    effect(() => {
      const cases = this.ws.cases();
      const d = this.ws.design();
      if (cases.length && d) {
        untracked(() => void this.loadHealth());
      }
    });
  }

  private async load(): Promise<void> {
    const d = this.ws.design();
    if (!d) {
      return;
    }
    try {
      this.result.set(await this.api.characterize(d, 5));
      this.error.set(null);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    }
  }

  private async loadHealth(): Promise<void> {
    const cases = this.ws.cases();
    try {
      const res = await this.api.analysis(cases.map((c) => c.id), this.ws.design(), null);
      const items: HealthItem[] = [];
      for (const a of res.analyses) {
        for (const c of a.checks) {
          if (c.level === 'warn' || c.level === 'error') {
            items.push({ caseId: a.case_id, caseName: a.name, check: c });
          }
        }
      }
      this.health.set(items);
    } catch {
      this.health.set(null);
    }
  }
}
