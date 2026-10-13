import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { ThemeService } from './core/theme.service';
import { WorkspaceService } from './core/workspace.service';

interface NavItem {
  path: string;
  label: string;
  step: string;
}

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly ws = inject(WorkspaceService);
  protected readonly theme = inject(ThemeService);

  protected readonly nav: NavItem[] = [
    { path: '/', label: 'Overview', step: '·' },
    { path: '/import', label: 'Data import', step: '1' },
    { path: '/analysis', label: 'Analysis', step: '2' },
    { path: '/model', label: 'Model & calibration', step: '3' },
    { path: '/optimizer', label: 'Optimizer', step: '4' },
    { path: '/materials', label: 'Materials', step: '5' },
    { path: '/lab', label: 'Lab planner', step: '6' },
  ];

  constructor() {
    void this.ws.init();
  }

  protected retry(): void {
    void this.ws.init();
  }

  protected themeLabel(): string {
    const c = this.theme.choice();
    return c === 'auto' ? 'Theme: auto' : c === 'light' ? 'Theme: light' : 'Theme: dark';
  }
}
