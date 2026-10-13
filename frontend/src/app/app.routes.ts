import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    title: 'Overview · teglab',
    loadComponent: () => import('./pages/home/home.page').then((m) => m.HomePage),
  },
  {
    path: 'import',
    title: 'Data import · teglab',
    loadComponent: () => import('./pages/import/import.page').then((m) => m.ImportPage),
  },
  {
    path: 'analysis',
    title: 'Analysis · teglab',
    loadComponent: () => import('./pages/analysis/analysis.page').then((m) => m.AnalysisPage),
  },
  {
    path: 'model',
    title: 'Model & calibration · teglab',
    loadComponent: () => import('./pages/model/model.page').then((m) => m.ModelPage),
  },
  {
    path: 'optimizer',
    title: 'Optimizer · teglab',
    loadComponent: () => import('./pages/optimizer/optimizer.page').then((m) => m.OptimizerPage),
  },
  {
    path: 'materials',
    title: 'Materials · teglab',
    loadComponent: () => import('./pages/materials/materials.page').then((m) => m.MaterialsPage),
  },
  {
    path: 'lab',
    title: 'Lab planner · teglab',
    loadComponent: () => import('./pages/lab/lab.page').then((m) => m.LabPage),
  },
  { path: '**', redirectTo: '' },
];
