import { ChangeDetectionStrategy, Component, inject, input, linkedSignal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { PipelineStateService } from '../../core/pipeline-state.service';
import { OverlayViewerComponent } from '../../shared/overlay-viewer.component';
import { NodalPanelComponent } from './nodal-panel.component';
import { ParametersPanelComponent } from './parameters-panel.component';

export type PipelineTab = 'parameters' | 'nodal' | 'overlay';

const TABS: readonly { id: PipelineTab; label: string }[] = [
  { id: 'parameters', label: 'Parameters' },
  { id: 'nodal', label: 'Nodal fields' },
  { id: 'overlay', label: 'Overlay' },
];

function isTab(value: string | undefined): value is PipelineTab {
  return TABS.some((t) => t.id === value);
}

/** Raw ANSYS exports → canonical data → physics, with nodal fields and PNG overlays (v0.2 pipeline). */
@Component({
  selector: 'app-pipeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ParametersPanelComponent, NodalPanelComponent, OverlayViewerComponent],
  templateUrl: './pipeline.page.html',
  styleUrl: './pipeline.page.css',
})
export class PipelinePage {
  /** `?tab=` query parameter, bound by the router (`withComponentInputBinding`). */
  readonly tab = input<string>();

  protected readonly state = inject(PipelineStateService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected readonly tabs = TABS;
  protected readonly active = linkedSignal<PipelineTab>(() => {
    const t = this.tab();
    return isTab(t) ? t : 'parameters';
  });

  constructor() {
    this.state.init();
  }

  protected select(tab: PipelineTab): void {
    this.active.set(tab);
    void this.router.navigate([], { relativeTo: this.route, queryParams: { tab }, replaceUrl: true });
  }

  protected async loadDemo(): Promise<void> {
    await this.state.demo('applied');
  }
}
