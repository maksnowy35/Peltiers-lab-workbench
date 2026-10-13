import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, Observable } from 'rxjs';

import {
  AnalysisResponse,
  CalibrationParam,
  CalibrationResult,
  CaseMeta,
  CaseSummary,
  Characterization,
  Constraints,
  EfficiencyMap,
  ExampleFile,
  ExportResult,
  GciResult,
  GeometryMode,
  LabCatalog,
  LabPlan,
  LabRequest,
  MapResult,
  MaterialInfo,
  ModelComparison,
  Objective,
  PairDesign,
  ParsedTable,
  ReferenceData,
  RoleInfo,
  SegmentedResult,
  Solution,
  SweepResult,
  ThomsonResult,
} from './models';

/** Thin typed wrapper around the FastAPI backend (all calls go to /api, proxied in development). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  private get<T>(path: string): Promise<T> {
    return this.call(this.http.get<T>(`${this.base}${path}`));
  }

  private post<T>(path: string, body: unknown): Promise<T> {
    return this.call(this.http.post<T>(`${this.base}${path}`, body));
  }

  private async call<T>(obs: Observable<T>): Promise<T> {
    try {
      return await firstValueFrom(obs);
    } catch (err) {
      throw new Error(describeError(err));
    }
  }

  reference(): Promise<ReferenceData> {
    return this.get('/reference');
  }

  roles(): Promise<RoleInfo[]> {
    return this.get('/roles');
  }

  characterize(design: PairDesign, nSweep = 25): Promise<Characterization> {
    return this.post('/model/characterize', { design, n_sweep: nSweep });
  }

  solve(design: PairDesign, currentA: number): Promise<Solution> {
    return this.post('/model/solve', { design, current_a: currentA, profiles: true });
  }

  compare(design: PairDesign, caseIds: string[], dense = 41): Promise<ModelComparison[]> {
    return this.post('/model/compare', { design, case_ids: caseIds, dense });
  }

  cases(): Promise<CaseSummary[]> {
    return this.get('/cases');
  }

  updateCase(id: string, patch: { name?: string; meta?: CaseMeta }): Promise<CaseSummary> {
    return this.call(this.http.patch<CaseSummary>(`${this.base}/cases/${id}`, patch));
  }

  deleteCase(id: string): Promise<{ deleted: boolean }> {
    return this.call(this.http.delete<{ deleted: boolean }>(`${this.base}/cases/${id}`));
  }

  reseed(): Promise<{ added: number }> {
    return this.post('/demo/seed', {});
  }

  parse(files: File[], text: string | null): Promise<ParsedTable> {
    const form = new FormData();
    for (const f of files) {
      form.append('files', f, f.name);
    }
    if (text) {
      form.append('text', text);
    }
    return this.post('/import/parse', form);
  }

  resuggest(table: ParsedTable): Promise<ParsedTable> {
    return this.post('/import/resuggest', table);
  }

  commit(body: {
    table: ParsedTable;
    cases: { index: number; name: string; meta: Partial<CaseMeta> }[];
    split_by: string | null;
    time_to_current: number | null;
  }): Promise<{ cases: CaseSummary[]; notes: string[] }> {
    return this.post('/import/commit', body);
  }

  examples(): Promise<ExampleFile[]> {
    return this.get('/import/examples');
  }

  exampleText(name: string): Promise<string> {
    return this.call(this.http.get(`${this.base}/import/examples/${encodeURIComponent(name)}`, { responseType: 'text' }));
  }

  analysis(caseIds: string[], design: PairDesign | null, referenceId: string | null): Promise<AnalysisResponse> {
    return this.post('/analysis', { case_ids: caseIds, design, reference_id: referenceId });
  }

  gci(body: { f_coarse: number; f_fine: number; r: number; p: number; fs: number | null; f_medium: number | null }): Promise<GciResult> {
    return this.post('/analysis/gci', body);
  }

  calibrationParams(): Promise<CalibrationParam[]> {
    return this.get('/calibration/params');
  }

  calibrate(design: PairDesign, caseIds: string[], fit: string[]): Promise<CalibrationResult> {
    return this.post('/calibration', { design, case_ids: caseIds, fit });
  }

  sweep(body: {
    design: PairDesign;
    mode: GeometryMode;
    values: number[];
    heights_mm: number[] | null;
    objective: Objective;
    constraints: Constraints;
    volume_mm3: number | null;
  }): Promise<SweepResult> {
    return this.post('/optimize/sweep', body);
  }

  map(body: {
    design: PairDesign;
    mode: GeometryMode;
    x_values: number[];
    heights_mm: number[];
    objective: Objective;
    constraints: Constraints;
    volume_mm3: number | null;
  }): Promise<MapResult> {
    return this.post('/optimize/map', body);
  }

  materials(): Promise<MaterialInfo[]> {
    return this.get('/materials');
  }

  uploadMaterial(file: File, name: string, kind: 'p' | 'n', tLimitC: number | null, density: number): Promise<MaterialInfo> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('name', name);
    form.append('kind', kind);
    form.append('density_g_cm3', String(density));
    if (tLimitC !== null) {
      form.append('t_limit_c', String(tLimitC));
    }
    return this.post('/materials/upload', form);
  }

  deleteMaterial(id: string): Promise<{ deleted: boolean }> {
    return this.call(this.http.delete<{ deleted: boolean }>(`${this.base}/materials/${id}`));
  }

  efficiencyMap(pairs: [string, string][], tColdC: number, tHotValuesC: number[]): Promise<EfficiencyMap> {
    return this.post('/materials/efficiency-map', { pairs, t_cold_c: tColdC, t_hot_values_c: tHotValuesC });
  }

  segmented(body: {
    p_hot: string;
    p_cold: string;
    n_hot: string;
    n_cold: string;
    t_hot_c: number;
    t_cold_c: number;
    fractions: number[];
    height_mm: number;
  }): Promise<SegmentedResult> {
    return this.post('/materials/segmented', body);
  }

  thomson(design: PairDesign): Promise<ThomsonResult> {
    return this.post('/materials/thomson', { design });
  }

  labCatalog(): Promise<LabCatalog> {
    return this.get('/lab/catalog');
  }

  labPlan(req: LabRequest): Promise<LabPlan> {
    return this.post('/lab/plan', req);
  }

  ansysExport(designs: object[], currents: number[], paramMap: Record<string, string> | null): Promise<ExportResult> {
    return this.post('/ansys/export', { designs, currents, param_map: paramMap });
  }
}

export function describeError(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const detail: unknown = err.error?.detail ?? err.error;
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length) {
      return detail.map((d: { msg?: string; loc?: unknown[] }) => `${(d.loc ?? []).join('.')}: ${d.msg ?? ''}`).join('; ');
    }
    if (err.status === 0) {
      return 'Backend not reachable – start it with "python -m teglab" (port 8000).';
    }
    return `${err.status} ${err.statusText}`;
  }
  return err instanceof Error ? err.message : String(err);
}
