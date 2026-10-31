import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, firstValueFrom } from 'rxjs';

import { describeError } from './api.service';
import {
  DeleteResult,
  ExampleInfo,
  ImageCalibration,
  LayerProfile,
  NodalDatasetInfo,
  NodalPoints,
  OverlayPlacement,
  PipelineReport,
  PointsRequest,
  ProfileRequest,
  ProjectProfile,
  RunSettings,
} from './pipeline.models';

/**
 * Typed client of `/api/pipeline`. Every request and response type comes from `pipeline.models.ts`,
 * which is generated from the backend's Pydantic models - the two sides cannot drift silently.
 */
@Injectable({ providedIn: 'root' })
export class PipelineApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pipeline';

  // ------------------------------------------------------------------ profiles & examples
  profiles(): Promise<string[]> {
    return this.call(this.http.get<string[]>(`${this.base}/profiles`));
  }

  profile(name: string): Promise<ProjectProfile> {
    return this.call(this.http.get<ProjectProfile>(`${this.base}/profiles/${encodeURIComponent(name)}`));
  }

  examples(): Promise<ExampleInfo[]> {
    return this.call(this.http.get<ExampleInfo[]>(`${this.base}/examples`));
  }

  exampleUrl(name: string): string {
    return `${this.base}/examples/${encodeURIComponent(name)}`;
  }

  async exampleFile(example: ExampleInfo): Promise<File> {
    const blob = await this.call(this.http.get(this.exampleUrl(example.name), { responseType: 'blob' }));
    return new File([blob], example.name, { type: blob.type });
  }

  // ------------------------------------------------------------------ parameter tables
  inspect(files: File[], profile: string): Promise<PipelineReport> {
    const form = this.form(files);
    form.append('profile', profile);
    return this.call(this.http.post<PipelineReport>(`${this.base}/inspect`, form));
  }

  run(files: File[], settings: RunSettings): Promise<PipelineReport> {
    const form = this.form(files);
    form.append('settings', JSON.stringify(settings));
    return this.call(this.http.post<PipelineReport>(`${this.base}/run`, form));
  }

  // ------------------------------------------------------------------ nodal datasets
  uploadNodal(files: File[]): Promise<NodalDatasetInfo> {
    return this.call(this.http.post<NodalDatasetInfo>(`${this.base}/nodal`, this.form(files)));
  }

  demoNodal(contacts: 'applied' | 'bonded'): Promise<NodalDatasetInfo> {
    return this.call(this.http.post<NodalDatasetInfo>(`${this.base}/nodal/demo`, null, { params: { contacts } }));
  }

  nodal(datasetId: string): Promise<NodalDatasetInfo> {
    return this.call(this.http.get<NodalDatasetInfo>(this.nodalPath(datasetId)));
  }

  deleteNodal(datasetId: string): Promise<DeleteResult> {
    return this.call(this.http.delete<DeleteResult>(this.nodalPath(datasetId)));
  }

  layerProfile(datasetId: string, req: ProfileRequest): Promise<LayerProfile> {
    return this.call(this.http.post<LayerProfile>(`${this.nodalPath(datasetId)}/profile`, req));
  }

  points(datasetId: string, req: PointsRequest): Promise<NodalPoints> {
    return this.call(this.http.post<NodalPoints>(`${this.nodalPath(datasetId)}/points`, req));
  }

  // ------------------------------------------------------------------ overlay
  overlay(calibration: ImageCalibration): Promise<OverlayPlacement> {
    return this.call(this.http.post<OverlayPlacement>(`${this.base}/overlay`, calibration));
  }

  private nodalPath(datasetId: string): string {
    return `${this.base}/nodal/${encodeURIComponent(datasetId)}`;
  }

  private form(files: File[]): FormData {
    const form = new FormData();
    for (const f of files) {
      form.append('files', f, f.name);
    }
    return form;
  }

  private async call<T>(obs: Observable<T>): Promise<T> {
    try {
      return await firstValueFrom(obs);
    } catch (err) {
      throw new Error(describeError(err));
    }
  }
}
