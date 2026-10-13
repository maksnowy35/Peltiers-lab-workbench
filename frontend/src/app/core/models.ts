/** Types mirroring the teglab REST API (backend/src/teglab). SI units unless the name says otherwise. */

export type HotMode = 'T' | 'Q' | 'HX';
export type ColdMode = 'T' | 'HX';
export type CheckLevel = 'ok' | 'info' | 'warn' | 'error';

export interface Segment {
  material: string;
  fraction: number;
}

export interface LegSpec {
  a_hot_mm: number;
  a_cold_mm: number;
  segments: Segment[];
}

export interface Contacts {
  active: boolean;
  tcc: number;
  ecc: number;
}

export interface Passive {
  ceramic_thickness_mm: number;
  ceramic_k: number;
  ceramic_area_mm2: number | null;
  cu_thickness_mm: number;
  cu_k: number;
  cu_rho: number;
  gap_mm: number;
  terminal_length_mm: number;
  hot_side_factor: number;
  cold_side_factor: number;
  r_el_extra_mohm: number;
}

export interface Boundary {
  hot_mode: HotMode;
  cold_mode: ColdMode;
  t_hot_c: number;
  q_hot_w: number;
  t_source_c: number;
  r_source_k_w: number;
  t_cold_c: number;
  t_sink_c: number;
  r_sink_k_w: number;
}

export interface PairDesign {
  name: string;
  height_mm: number;
  p_leg: LegSpec;
  n_leg: LegSpec;
  contacts: Contacts;
  passive: Passive;
  boundary: Boundary;
  c3d: number;
  alpha_factor: number;
  side_loss_h: number;
  t_ambient_c: number;
  n_elements: number;
}

export interface LegProfile {
  x_mm: number[];
  t_c: number[];
  a_mm: number[];
  x_el_mm: number[];
  j_a_m2: number[];
  joule_w_m3: number[];
  alpha_uv_k: number[];
  tau_uv_k: number[];
}

export interface Solution {
  current_a: number;
  voltage_v: number;
  power_w: number;
  q_hot_w: number;
  q_cold_w: number;
  q_side_w: number;
  efficiency: number;
  exergy_efficiency: number;
  carnot: number;
  emf_v: number;
  r_int_ohm: number;
  load_resistance_ohm: number | null;
  t_hot_ext_c: number;
  t_cold_ext_c: number;
  t_hot_plate_c: number;
  t_cold_plate_c: number;
  t_leg_hot_c: number[];
  t_leg_cold_c: number[];
  dt_te_k: number;
  dt_passive_hot_k: number;
  dt_passive_cold_k: number;
  t_max_c: number;
  j_max_a_m2: number;
  energy_balance_rel: number;
  iterations: number;
  converged: boolean;
  warnings: string[];
  profiles?: { P: LegProfile; N: LegProfile };
}

export interface MaterialTemperature {
  state?: string;
  leg: string;
  material: string;
  name: string;
  t_max_c: number;
  t_min_c: number;
  t_limit_c: number;
  ok: boolean;
}

export interface CharacterizeSummary {
  u_oc_v: number;
  r_int_ohm: number;
  k_pair_w_k: number;
  p_max_w: number;
  i_opt_a: number;
  i_sc_a: number;
  r_load_opt_ohm: number;
  eta_at_p_max: number;
  eta_max: number;
  i_eta_max_a: number;
  exergy_eta_at_p_max: number;
  t_hot_ext_oc_c: number;
  t_max_oc_c: number;
  dt_te_oc_k: number;
  te_volume_mm3: number;
  volume_per_power_mm3_mw: number | null;
  power_per_volume_mw_mm3: number | null;
}

export interface Characterization {
  open_circuit: Solution;
  max_power: Solution;
  max_efficiency: Solution;
  sweep: Solution[];
  summary: CharacterizeSummary;
  material_temperatures: MaterialTemperature[];
  passive: {
    r_ceramic_hot_k_w: number;
    r_ceramic_cold_k_w: number;
    r_branch_hot_k_w: number[];
    r_branch_cold_k_w: number[];
    r_contact_el_ohm: number;
    r_cu_el_ohm: number;
    r_el_extra_ohm: number;
  };
  geometry: {
    leg: string;
    a_hot_mm: number;
    a_cold_mm: number;
    height_mm: number;
    theta_deg: number;
    volume_mm3: number;
    materials: string[];
  }[];
}

export interface CaseMeta {
  kind: 'design_points' | 'profile' | 'measurement';
  bc: 'TT' | 'QT' | 'other';
  t_hot_c: number | null;
  t_cold_c: number | null;
  q_hot_w: number | null;
  a_hot_mm: number | null;
  a_cold_mm: number | null;
  height_mm: number | null;
  contacts_active: boolean | null;
  variant: string | null;
  mesh: string | null;
  leg: 'P' | 'N' | null;
  current_a: number | null;
  source: string;
  notes: string;
}

export interface CaseSummary {
  id: string;
  name: string;
  meta: CaseMeta;
  n_points: number;
  roles: string[];
  created: string;
}

export interface RoleInfo {
  key: string;
  label: string;
  dimension: string;
  group: string;
  description: string;
}

export interface ParsedColumn {
  key: string;
  name: string;
  pid: string | null;
  unit: string | null;
  values: (number | null)[];
  numeric: boolean;
  text: string[];
  role: string;
  case: number;
  confidence: number;
}

export interface ParsedTable {
  format: string;
  columns: ParsedColumn[];
  row_labels: string[];
  n_rows: number;
  notes: string[];
  sources: string[];
  n_cases: number;
  case_names: string[];
  suggested_meta: Partial<CaseMeta>[];
}

export interface ExampleFile {
  name: string;
  description: string;
  size: number;
}

export interface Check {
  id: string;
  level: CheckLevel;
  title: string;
  detail: string;
  value?: number;
  expected?: number;
  alternative?: number;
  currents?: number[];
}

export type Series = (number | null)[];

export interface CaseAnalysis {
  case_id: string;
  name: string;
  meta: CaseMeta;
  points: {
    current_a: Series;
    voltage_v: Series;
    power_w: Series;
    q_hot_w: Series;
    q_cold_w: Series;
    efficiency: Series;
    exergy_efficiency: Series;
    balance_rel: Series;
    t_hot_ext_c: Series;
    t_cold_ext_c: Series;
    t_leg_hot_c: Series;
    t_leg_cold_c: Series;
    dt_te_k: Series;
    t_max_c: Series;
    j_max_a_m2: Series;
  };
  curve: {
    n_points: number;
    linear?: { u0_v: number; r_ohm: number; r2: number; i_sc_a: number | null; i_opt_a: number | null; p_max_w: number | null };
    best?: { method: string; i_opt_a: number; p_max_w: number } | null;
    p_max_spread_rel?: number | null;
  };
  metrics: {
    u_oc_v: number | null;
    dt_te_oc_k: number | null;
    q_hot_oc_w: number | null;
    t_hot_ext_oc_c: number | null;
    k_pair_w_k: number | null;
    alpha_eff_v_k: number | null;
    p_max_w: number | null;
    i_opt_a: number | null;
    p_max_method: string | null;
    r_int_fit_ohm: number | null;
    r2_linear: number | null;
    passive_share?: number;
    eta_max_sampled?: number;
    i_eta_max_sampled_a?: number;
    eta_at_p_max?: number;
    exergy_eta_max_sampled?: number;
    te_volume_mm3?: number;
    volume_per_power_mm3_mw?: number;
    power_per_volume_mw_mm3?: number;
    geometry?: { a_hot_mm: number; a_cold_mm: number; height_mm: number; theta_deg: number };
  };
  checks: Check[];
}

export interface ComparisonRow {
  case_id: string;
  name: string;
  theta_deg: number | null;
  k_pair_w_k: number | null;
  u_oc_v: number | null;
  dt_te_oc_k: number | null;
  t_hot_ext_oc_c: number | null;
  p_max_w: number | null;
  i_opt_a: number | null;
  eta_at_p_max: number | null;
  te_volume_mm3: number | null;
  power_per_volume_mw_mm3: number | null;
  volume_per_power_mm3_mw: number | null;
  d_k_pair_w_k: number | null;
  d_u_oc_v: number | null;
  d_p_max_w: number | null;
  d_te_volume_mm3: number | null;
  d_power_per_volume_mw_mm3: number | null;
  d_eta_at_p_max: number | null;
}

export interface AnalysisResponse {
  analyses: CaseAnalysis[];
  comparison: { rows: ComparisonRow[]; reference: string | null };
}

export interface GciResult {
  p_used: number;
  p_observed?: number | null;
  fs: number;
  gci_fine: number;
  gci_medium?: number;
  asymptotic_ratio?: number | null;
  f_extrapolated: number;
  rel_change: number;
}

export interface DenseCurve {
  current_a: number[];
  voltage_v: number[];
  power_w: number[];
  q_hot_w: number[];
  q_cold_w: number[];
  t_hot_ext_c: number[];
  dt_te_k: number[];
  efficiency: number[];
}

export interface ModelComparison {
  case_id: string;
  name: string;
  observed: Record<string, Series>;
  predicted: Record<string, Series>;
  dense: DenseCurve | null;
  errors: Record<string, { rms_rel: number; max_abs: number }>;
  design: PairDesign;
}

export interface CalibrationParam {
  key: string;
  label: string;
  min: number;
  max: number;
  log: boolean;
}

export interface CalibrationResult {
  fit: string[];
  initial: Record<string, number>;
  params: Record<string, number>;
  cost_before: number;
  cost_after: number;
  rms_before: number;
  rms_after: number;
  success: boolean;
  message: string;
  nfev: number;
  before: ModelComparison[];
  after: ModelComparison[];
  design: PairDesign;
}

export type GeometryMode = 'fixed_cold_base' | 'fixed_hot_base' | 'constant_volume';
export type Objective = 'p_max' | 'eta_at_p_max' | 'power_per_volume';

export interface Constraints {
  a_min_mm: number;
  t_joint_max_c: number | null;
  respect_material_limit: boolean;
  theta_max_deg: number | null;
}

export interface DesignPoint {
  x?: number;
  a_hot_mm: number;
  a_cold_mm: number;
  height_mm: number;
  theta_deg: number;
  ratio: number;
  te_volume_mm3: number;
  k_pair_w_k: number | null;
  u_oc_v: number;
  r_int_ohm: number;
  p_max_w: number;
  i_opt_a: number;
  eta_at_p_max: number;
  exergy_eta_at_p_max: number;
  q_hot_w: number;
  t_hot_ext_oc_c: number;
  t_hot_ext_pmax_c: number;
  t_joint_max_c: number;
  t_max_c: number;
  dt_te_pmax_k: number;
  j_max_a_m2: number;
  power_per_volume_mw_mm3: number | null;
  volume_per_power_mm3_mw: number | null;
  feasible: boolean;
  violations: string[];
}

export interface SweepResult {
  mode: GeometryMode;
  variable: 'ratio' | 'angle_deg';
  objective: Objective;
  series: { height_mm: number; points: DesignPoint[] }[];
  optimum: DesignPoint | null;
  reference: DesignPoint;
  pareto: { te_volume_mm3: number; p_max_w: number; theta_deg: number; height_mm: number; a_hot_mm: number; a_cold_mm: number }[];
}

export interface MapResult {
  mode: GeometryMode;
  objective: Objective;
  x: number[];
  y: number[];
  z: (number | null)[][];
  feasible: boolean[][];
  t_joint_c: (number | null)[][];
  optimum: DesignPoint | null;
}

export interface MaterialInfo {
  id: string;
  name: string;
  kind: 'p' | 'n';
  family: string;
  t_min_c: number;
  t_max_c: number;
  t_limit_c: number;
  density_g_cm3: number;
  constant: boolean;
  notes: string;
  source: string;
  zt_peak: number;
  curve: {
    t_c: number[];
    alpha_uv_k: number[];
    rho_uohm_m: number[];
    kappa: number[];
    zt: number[];
    s_per_v: number[];
  };
}

export interface EfficiencyPoint {
  t_hot_c: number;
  eta_max: number;
  eta_ideal_zt: number;
  carnot: number;
  p_max_w: number;
  power_density_w_cm2: number;
  u_oc_v: number;
  within_limits: boolean;
}

export interface EfficiencyMap {
  t_cold_c: number;
  series: { p: string; n: string; label: string; n_area_ratio: number; points: EfficiencyPoint[] }[];
}

export interface SegmentedRow {
  fraction_hot: number;
  eta_max: number;
  p_max_w: number;
  u_oc_v: number;
  t_interface_c: number | null;
  materials: MaterialTemperature[];
  feasible: boolean;
}

export interface SegmentedResult {
  t_hot_c: number;
  t_cold_c: number;
  rows: SegmentedRow[];
  optimum: SegmentedRow | null;
}

export interface ThomsonResult {
  with_thomson: { p_max_w: number; eta_max: number; u_oc_v: number; q_hot_pmax_w: number };
  without_thomson: { p_max_w: number; eta_max: number; u_oc_v: number; q_hot_pmax_w: number };
  delta_p_rel: number | null;
  delta_eta_rel: number | null;
  thomson_heat_w: number;
  note: string;
}

export interface Solder {
  id: string;
  name: string;
  solidus_c: number;
  liquidus_c: number;
  density: number;
  k: number;
  rho: number;
  pb_free: boolean;
  soak_c: number[];
  peak_c: number[];
  note: string;
}

export interface Ceramic {
  id: string;
  name: string;
  k: number;
  density: number;
}

export interface LabCatalog {
  solders: Solder[];
  ceramics: Ceramic[];
}

export interface LabRequest {
  design: PairDesign;
  n_pairs: number;
  spare_fraction: number;
  kerf_mm: number;
  solder_id: string;
  joint_thickness_um: number;
  fillet_factor: number;
  paste_metal_mass_fraction: number;
  ni_thickness_um: number;
  ceramic_id: string;
  cu_on_ceramic: 'dbc' | 'soldered';
  test_mode: 'TT' | 'QT';
  t_hot_test_c: number;
  t_cold_test_c: number;
  q_hot_test_w: number;
  t_ambient_c: number;
  supply_voltage_v: number;
  heater_safety_factor: number;
  heater_block_area_cm2: number;
  emissivity: number;
  h_conv: number;
  insulation_loss_fraction: number;
  coolant: 'water' | 'air';
  coolant_dt_k: number;
  joint_margin_k: number;
  clamp_pressure_mpa: number;
  sigma_t_k: number;
  sigma_u_rel: number;
  sigma_u_abs_v: number;
  sigma_i_rel: number;
  sigma_q_rel: number;
}

export interface LabLeg {
  type: string;
  materials: string[];
  count: number;
  spares: number;
  a_hot_mm: number;
  a_cold_mm: number;
  height_mm: number;
  theta_deg: number;
  volume_mm3: number;
  raw_volume_per_leg_mm3: number;
  material_mass_g: number;
  fragile: boolean;
}

export interface SolderCheck {
  id: string;
  name: string;
  solidus_c: number;
  margin_k: number;
  homologous: number;
  ok: boolean;
  pb_free: boolean;
}

export interface LabPlan {
  design_name: string;
  test: { mode: 'TT' | 'QT'; t_hot_c: number | null; q_hot_w_per_pair: number | null; t_cold_c: number };
  expected: {
    per_pair: {
      u_oc_v: number;
      r_int_ohm: number;
      i_sc_estimate_a: number;
      i_opt_a: number;
      p_max_w: number;
      eta_at_p_max: number;
      q_hot_w: number;
      t_hot_ext_c: number;
      t_leg_hot_c: number;
      t_leg_cold_c: number;
      dt_te_k: number;
    };
    module: { n_pairs: number; u_oc_v: number; r_int_ohm: number; p_max_w: number };
  };
  build: {
    legs: LabLeg[];
    te_material_total_g: number;
    electrodes: { count: number; length_mm: number; width_mm: number; thickness_mm: number; cu_mass_g: number };
    ceramic: { material: string; plates: number; width_mm: number; length_mm: number; thickness_mm: number; mass_g: number };
    nickel: { thickness_um: number; area_mm2: number; mass_mg: number };
    layout: { rows: number; cols: number; pitch_mm: number };
  };
  solder: {
    alloy: string;
    joints_te_cu: number;
    joints_cu_ceramic: number;
    joint_area_mm2: number;
    volume_mm3: number;
    metal_mass_g: number;
    paste_mass_g: number;
    paste_with_waste_g: number;
    stencil_thickness_um: number;
    reflow: { soak_c: number[]; peak_c: number[]; time_above_liquidus_s: number[]; atmosphere: string };
    t_joint_max_c: number;
    t_leg_hot_end_max_c: number;
    margin_to_solidus_k: number;
    homologous_temperature: number;
    ok: boolean;
    alternatives: SolderCheck[];
    warnings: string[];
  };
  heater: {
    q_pairs_w: number;
    q_radiation_w: number;
    q_convection_w: number;
    q_insulation_w: number;
    q_required_w: number;
    rated_power_w: number;
    supply_v: number;
    resistance_ohm: number;
    current_a: number;
    block_temperature_c: number;
    control: string;
  };
  cooling: {
    q_reject_w: number;
    medium?: string;
    flow_ml_min?: number;
    coolant_dt_k?: number;
    max_heatsink_r_k_w?: number | null;
    note?: string;
  };
  clamping: { pressure_mpa: number; plate_area_mm2: number; force_n: number };
  electrical: {
    method: string;
    current_setpoints_a: number[];
    load_resistances_ohm: number[];
    expected_sweep: { current_a: number; voltage_v: number; power_w: number; load_ohm: number | null; t_hot_ext_c: number; q_hot_w: number }[];
    shunt_ohm: number;
    shunt_power_w: number;
    voltmeter_resolution_v: number;
    notes: string[];
  };
  thermocouples: { position: string; type: string; count: number; expected_c?: number }[];
  uncertainty: {
    rel_power: number | null;
    rel_efficiency: number | null;
    rel_alpha_eff: number | null;
    rel_power_from_temperature: number | null;
  };
  run_sheet_md: string;
  measurement_template_csv: string;
  bom_csv: string;
}

export interface ExportResult {
  rows: { name: string; a_hot_mm: number; a_cold_mm: number; height_mm: number; theta_deg: number; current_a: number }[];
  csv: string;
  journal: string;
  param_map: Record<string, string>;
}

export interface ReferenceData {
  design: PairDesign;
  table_5_4: Record<string, { t_hot_ext_c: number; dt_te_k: number; k_pair_w_k: number; u_oc_v: number; p_max_w: number; eta: number }>;
  table_4_2: Record<string, number>;
  variants: Record<string, { a_hot_mm: number; a_cold_mm: number; height_mm: number; theta_deg: number }>;
  calibrated_a0: Record<string, number>;
}
