"""Nodal datasets: ingest Mechanical exports, infer the layer stack, evaluate profiles and contacts.

A *dataset* is one analysis system exported body by body (``tools/ansys/teglab_export_nodal.py`` writes
``<body>__<quantity>.txt`` files, contour PNGs and a ``manifest.json``; a zip of that folder is the unit of
upload).  Ingestion is tolerant: without a manifest the body comes from the ``<body>__`` file-name prefix,
or from the file name itself, and files that share every node number are merged into one body.

Pipeline::

    files / zip ──► readers.read_source ──► per-body frames (melt → pivot on node_id)
                                              │
                          infer_stack ◄───────┘  (axis from interval clustering, hot side from T)
                               │
         layer_profile ◄───────┴───────► contact checks (TCC_eff = q/ΔT, ECC_eff = J/ΔV)
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

import numpy as np
import pandas as pd

from ..units import CANONICAL
from .fields import Layer, contact_state, find_interfaces, layer_profile, passive_active_split
from .keys import NODAL_DIMENSION, NodalKey
from .overlay import PNG_SIGNATURE, png_size
from .readers import NodalTable, read_source
from .schemas import (
    Axis,
    BodyInfo,
    ContactCheck,
    ContactProfile,
    ContactVerdict,
    DatasetImage,
    ImageSpec,
    Issue,
    LayerProfile,
    LayerRow,
    LayerSplit,
    LayerStack,
    Level,
    NodalDatasetInfo,
    NodalFileInfo,
    NodalManifest,
    NodalPoints,
    PointsRequest,
    ProfileRequest,
    StackLayer,
    View,
)

__all__ = [
    "VIEW_AXES",
    "DatasetStore",
    "NodalDataset",
    "build_dataset",
    "demo_dataset",
    "evaluate_profile",
    "infer_stack",
    "project_points",
]

COORDS = ["x_mm", "y_mm", "z_mm"]
AXES: tuple[Axis, ...] = ("x", "y", "z")
MAX_ARCHIVE_MEMBERS = 2000
MAX_UNCOMPRESSED_BYTES = 512 * 2**20
MAX_IMAGE_BYTES = 25 * 2**20
FLUX_FLOOR = 1.0  # W/m² or A/m²: below this a conductance is not evaluated
BONDED_FRACTION = 0.05  # jump < 5 % of the expected jump → no contact resistance was applied
AS_SPECIFIED = (2.0 / 3.0, 1.5)  # accepted range of effective/expected conductance

# (horizontal axis, sign), (vertical axis, sign), (depth axis towards the camera, sign) of the standard
# Mechanical views with +Y up.  tools/ansys/teglab_export_nodal.py uses the same table for its cameras.
VIEW_AXES: dict[View, tuple[tuple[Axis, int], tuple[Axis, int], tuple[Axis, int]]] = {
    "front": (("x", 1), ("y", 1), ("z", 1)),
    "back": (("x", -1), ("y", 1), ("z", -1)),
    "right": (("z", -1), ("y", 1), ("x", 1)),
    "left": (("z", 1), ("y", 1), ("x", -1)),
    "top": (("x", 1), ("z", -1), ("y", 1)),
    "bottom": (("x", 1), ("z", 1), ("y", -1)),
}


def _issue(level: Level, code: str, message: str, source: str | None = None) -> Issue:
    return Issue(level=level, code=code, message=message, source=source)


# ------------------------------------------------------------------------------------------ dataset
@dataclass
class StoredImage:
    info: DatasetImage
    data: bytes


@dataclass
class NodalDataset:
    dataset_id: str
    title: str | None
    synthetic: bool
    files: list[NodalFileInfo]
    bodies: dict[str, pd.DataFrame]
    proposed_stack: LayerStack | None
    images: dict[str, StoredImage] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def quantities(self) -> list[NodalKey]:
        present = {k for f in self.bodies.values() for k in NodalKey if k.value in f and f[k.value].notna().any()}
        return [k for k in NodalKey if k in present]

    def body_info(self) -> list[BodyInfo]:
        out = []
        for tag, f in self.bodies.items():
            t = f["temperature_c"].mean() if "temperature_c" in f else None
            out.append(
                BodyInfo(
                    tag=tag,
                    n_nodes=len(f),
                    quantities=[k for k in NodalKey if k.value in f and f[k.value].notna().any()],
                    min_mm=tuple(float(f[c].min()) for c in COORDS),
                    max_mm=tuple(float(f[c].max()) for c in COORDS),
                    t_mean_c=None if t is None or pd.isna(t) else float(t),
                )
            )
        return out

    def info(self) -> NodalDatasetInfo:
        return NodalDatasetInfo(
            dataset_id=self.dataset_id,
            title=self.title,
            synthetic=self.synthetic,
            files=self.files,
            bodies=self.body_info(),
            quantities=self.quantities,
            proposed_stack=self.proposed_stack,
            images=[img.info for img in self.images.values()],
            issues=self.issues,
        )


# ---------------------------------------------------------------------------------------- ingestion
def _expand(files: list[tuple[str, bytes]], issues: list[Issue]) -> list[tuple[str, bytes]]:
    """Unpack zip archives (with limits against zip bombs); other files pass through."""
    out: list[tuple[str, bytes]] = []
    for name, data in files:
        if not (name.lower().endswith(".zip") and data[:4] == b"PK\x03\x04"):
            out.append((name, data))
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{name}: not a valid zip archive") from exc
        members = [
            m
            for m in zf.infolist()
            if not m.is_dir() and "__MACOSX" not in m.filename and not PurePosixPath(m.filename).name.startswith(".")
        ]
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError(f"{name}: more than {MAX_ARCHIVE_MEMBERS} files")
        if sum(m.file_size for m in members) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError(f"{name}: more than {MAX_UNCOMPRESSED_BYTES // 2**20} MB uncompressed")
        for m in members:
            out.append((m.filename, zf.read(m)))
        issues.append(_issue(Level.INFO, "archive", f"{len(members)} files unpacked", name))
    return out


def _base(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).name


def _tag(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "body"


def _body_from_name(name: str) -> tuple[str, bool]:
    """Body tag from '<body>__<quantity>.txt' (True) or from the whole stem (False)."""
    stem = PurePosixPath(_base(name)).stem
    if "__" in stem:
        return _tag(stem.split("__", 1)[0]), True
    return _tag(stem), False


def _merge_body(tag: str, tables: list[NodalTable], issues: list[Issue]) -> pd.DataFrame:
    """One frame per body: coordinates once, one column per quantity, repeated node numbers averaged."""
    coords = pd.concat([t.frame[["node_id", *COORDS]] for t in tables], ignore_index=True)
    grouped = coords.groupby("node_id")
    spread = (grouped[COORDS].max() - grouped[COORDS].min()).to_numpy().max(initial=0.0)
    if spread > 1e-3:
        issues.append(
            _issue(
                Level.ERROR,
                "node-mismatch",
                f"body '{tag}': the same node numbers have coordinates up to {spread:.3g} mm apart - "
                "the files belong to different bodies or meshes",
            )
        )
    xyz = grouped[COORDS].mean()
    long = pd.concat(
        [
            t.frame.melt(id_vars="node_id", value_vars=[k.value for k in t.quantities], var_name="q", value_name="v")
            for t in tables
            if t.quantities
        ],
        ignore_index=True,
    )
    if long.empty:
        return xyz.reset_index()
    wide = long.pivot_table(index="node_id", columns="q", values="v", aggfunc="mean")
    wide.columns.name = None
    return xyz.join(wide).reset_index()


def build_dataset(
    files: list[tuple[str, bytes]], *, synthetic: bool = False, dataset_id: str | None = None
) -> NodalDataset:
    """Parse uploads (text exports, zip archives, manifest.json, PNG contours) into a dataset."""
    dataset_id = dataset_id or uuid.uuid4().hex
    issues: list[Issue] = []
    entries = _expand(files, issues)

    manifests = [(n, d) for n, d in entries if _base(n).lower() == "manifest.json"]
    if len(manifests) > 1:
        raise ValueError(f"{len(manifests)} manifests found: upload one analysis system (one export folder) at a time")
    manifest = NodalManifest()
    if manifests:
        try:
            manifest = NodalManifest.model_validate_json(manifests[0][1])
        except ValueError as exc:
            issues.append(_issue(Level.ERROR, "manifest", f"manifest.json ignored: {exc}", manifests[0][0]))
    listed = {_base(s.file): s for s in manifest.files}
    listed_images = {_base(s.file): s for s in manifest.images}

    by_body: dict[str, list[NodalTable]] = {}
    from_stem: set[str] = set()
    file_infos: list[NodalFileInfo] = []
    pngs: list[tuple[str, bytes]] = []
    for name, data in entries:
        if _base(name).lower() == "manifest.json":
            continue
        if data.startswith(PNG_SIGNATURE):
            pngs.append((name, data))
            continue
        try:
            table = read_source(data, _base(name))
        except ValueError as exc:
            issues.append(_issue(Level.WARNING, "skipped", f"skipped: {exc}", name))
            continue
        if not isinstance(table, NodalTable):
            issues.append(
                _issue(Level.WARNING, "not-nodal", "a parameter table, not a nodal export: use the parameter run", name)
            )
            continue
        spec = listed.get(_base(name))
        if spec is not None:
            body = _tag(spec.body)
            if spec.quantity is not None and spec.quantity not in table.quantities:
                issues.append(
                    _issue(Level.WARNING, "manifest-quantity", f"manifest says {spec.quantity}, header disagrees", name)
                )
        else:
            body, by_convention = _body_from_name(name)
            if not by_convention:
                from_stem.add(body)
        by_body.setdefault(body, []).append(table)
        file_infos.append(
            NodalFileInfo(
                file=name,
                body=body,
                quantities=list(table.quantities),
                n_rows=len(table.frame),
                averaged=table.averaged,
                notes=table.notes,
            )
        )

    _merge_same_nodes(by_body, from_stem, file_infos, issues)
    bodies = {tag: _merge_body(tag, tables, issues) for tag, tables in by_body.items()}
    if not bodies:
        raise ValueError("no Mechanical nodal export found (expected 'Node Number … X Location …' text files)")
    if not any("temperature_c" in f for f in bodies.values()):
        issues.append(_issue(Level.WARNING, "no-temperature", "no temperature export: layer profiles need one"))

    stack, stack_issues = infer_stack(bodies, manifest.axis)
    issues.extend(stack_issues)
    ds = NodalDataset(
        dataset_id=dataset_id,
        title=manifest.system or (f"{len(file_infos)} nodal files" if file_infos else None),
        synthetic=synthetic or manifest.synthetic,
        files=file_infos,
        bodies=bodies,
        proposed_stack=stack,
        issues=issues,
    )
    for name, data in pngs:
        _add_image(ds, name, data, listed_images.get(_base(name)), synthetic)
    return ds


def _merge_same_nodes(
    by_body: dict[str, list[NodalTable]], from_stem: set[str], infos: list[NodalFileInfo], issues: list[Issue]
) -> None:
    """Files named without a body prefix but covering identical nodes are one body (e.g. T.txt + V.txt)."""
    signatures: dict[str, str] = {}
    for tag in sorted(from_stem & set(by_body)):
        ids = np.unique(pd.concat([t.frame["node_id"] for t in by_body[tag]]).to_numpy(dtype="int64"))
        sig = hashlib.sha1(ids.tobytes()).hexdigest()
        if sig in signatures:
            keep = signatures[sig]
            by_body[keep].extend(by_body.pop(tag))
            for i, info in enumerate(infos):
                if info.body == tag:
                    infos[i] = info.model_copy(update={"body": keep})
            issues.append(
                _issue(Level.INFO, "merged", f"'{tag}' shares every node number with '{keep}': merged as '{keep}'")
            )
        else:
            signatures[sig] = tag


def _add_image(ds: NodalDataset, name: str, data: bytes, spec: ImageSpec | None, synthetic: bool) -> None:
    base = _base(name)
    if len(data) > MAX_IMAGE_BYTES:
        ds.issues.append(_issue(Level.WARNING, "image-too-large", "image skipped (larger than 25 MB)", name))
        return
    try:
        width, height = png_size(data)
    except ValueError as exc:
        ds.issues.append(_issue(Level.WARNING, "image-invalid", f"image skipped: {exc}", name))
        return
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base)
    cal = spec.calibration if spec else None
    if cal is not None and (cal.width_px, cal.height_px) != (width, height):
        ds.issues.append(
            _issue(Level.WARNING, "calibration-size", "calibration is for another image size; ignored", name)
        )
        cal = None
    source = "none" if cal is None else ("exact" if synthetic else spec.calibration_source)
    info = DatasetImage(
        name=safe,
        url=f"/api/pipeline/nodal/{ds.dataset_id}/images/{safe}",
        view=spec.view if spec else None,
        quantity=spec.quantity if spec else None,
        width_px=width,
        height_px=height,
        calibration=cal,
        calibration_source=source,
    )
    ds.images[safe] = StoredImage(info, data)


# ----------------------------------------------------------------------------------- stack inference
def _clusters(intervals: dict[str, tuple[float, float]], tol: float) -> list[list[str]]:
    """Connected groups of overlapping intervals (touching end to end does not count as overlap)."""
    groups: list[list[str]] = []
    end = -np.inf
    for tag, (lo, hi) in sorted(intervals.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        if groups and lo < end - tol:
            groups[-1].append(tag)
            end = max(end, hi)
        else:
            groups.append([tag])
            end = hi
    return groups


def _layer_names(groups: list[list[str]], active: int) -> list[str]:
    n = len(groups)
    if n == 5 and active == 2:
        return ["ceramic hot", "Cu hot", "legs", "Cu cold", "ceramic cold"]
    if n == 3 and active == 1:
        return ["Cu hot", "legs", "Cu cold"]
    names = []
    for k, tags in enumerate(groups):
        if k == active:
            names.append("legs" if len(tags) > 1 else tags[0])
            continue
        prefix = re.sub(r"[_\s]+$", "", _common_prefix(tags)) if len(tags) > 1 else tags[0]
        names.append(prefix or f"layer {k + 1}")
    seen: dict[str, int] = {}
    for k, name in enumerate(names):  # keep names unique
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            names[k] = f"{name} ({seen[name]})"
    return names


def _common_prefix(tags: list[str]) -> str:
    first, last = min(tags), max(tags)
    n = 0
    while n < min(len(first), len(last)) and first[n] == last[n]:
        n += 1
    return first[:n]


def infer_stack(bodies: dict[str, pd.DataFrame], axis: Axis | None = None) -> tuple[LayerStack | None, list[Issue]]:
    """Propose layers (hot → cold) from body extents; temperature decides the hot side and the legs."""
    issues: list[Issue] = []
    if len(bodies) < 2:
        return None, [_issue(Level.INFO, "single-body", "one body only: no layer stack to propose")]
    lo = {tag: f[COORDS].min() for tag, f in bodies.items()}
    hi = {tag: f[COORDS].max() for tag, f in bodies.items()}
    span = max(max(hi[t][c] for t in bodies) - min(lo[t][c] for t in bodies) for c in COORDS)
    tol = 1e-3 * span

    def groups_along(ax: Axis) -> list[list[str]]:
        return _clusters({t: (lo[t][f"{ax}_mm"], hi[t][f"{ax}_mm"]) for t in bodies}, tol)

    has_t = all("temperature_c" in f and f["temperature_c"].notna().any() for f in bodies.values())
    if axis is None:
        counts = {ax: len(groups_along(ax)) for ax in AXES}
        best = max(counts.values())
        candidates = [ax for ax in AXES if counts[ax] == best]
        if best < 2:
            return None, [_issue(Level.WARNING, "no-stack", "the bodies do not form layers along x, y or z")]
        if len(candidates) > 1 and has_t:
            both = pd.concat([f[[*COORDS, "temperature_c"]] for f in bodies.values()]).dropna()
            both = both.sample(min(len(both), 20_000), random_state=0)
            corr = {ax: abs(both[f"{ax}_mm"].corr(both["temperature_c"])) for ax in candidates}
            axis = max(candidates, key=lambda ax: np.nan_to_num(corr[ax]))
        else:
            axis = "y" if "y" in candidates else candidates[0]
        if len(candidates) > 1:
            issues.append(_issue(Level.INFO, "axis-ambiguous", f"layers along {candidates}; chose {axis}"))
    groups = groups_along(axis)
    if len(groups) < 2:
        return None, [_issue(Level.WARNING, "no-stack", f"the bodies do not form layers along {axis}")]

    col = f"{axis}_mm"
    if has_t:
        temps = [pd.concat([bodies[b]["temperature_c"] for b in g]) for g in groups]
        if temps[0].mean() < temps[-1].mean():  # groups are sorted by coordinate: put the hot end first
            groups.reverse()
            temps.reverse()
        active = int(np.nanargmax([t.max() - t.min() for t in temps]))  # the legs carry the largest ΔT
    else:
        groups.reverse()  # without temperatures: assume the hot face is at the largest coordinate
        thickness = [max(hi[b][col] for b in g) - min(lo[b][col] for b in g) for g in groups]
        inner = range(1, len(groups) - 1) if len(groups) > 2 else range(len(groups))
        active = max(inner, key=lambda k: thickness[k])
        issues.append(
            _issue(
                Level.WARNING,
                "orientation-assumed",
                f"no temperatures: assumed the hot face at the largest {axis} and the thickest inner layer as legs",
            )
        )
    names = _layer_names(groups, active)
    layers = [
        StackLayer(name=name, bodies=sorted(g), active=(k == active))
        for k, (name, g) in enumerate(zip(names, groups, strict=True))
    ]
    return LayerStack(axis=axis, layers=layers), issues


# ------------------------------------------------------------------------------------ layer profile
def _verdict(flux: float | None, jump: float | None, expected: float) -> tuple[float | None, ContactVerdict]:
    if flux is None or jump is None or flux < FLUX_FLOOR:
        return None, "not-evaluated"
    expected_jump = flux / expected
    if abs(jump) < BONDED_FRACTION * expected_jump:  # None = no measurable jump (numerically infinite)
        return (flux / abs(jump) if abs(jump) > 1e-6 * expected_jump else None), "bonded"
    effective = flux / abs(jump)
    ratio = effective / expected
    return effective, ("as-specified" if AS_SPECIFIED[0] <= ratio <= AS_SPECIFIED[1] else "deviates")


def _none(value: float | None) -> float | None:
    return None if value is None or not np.isfinite(value) else float(value)


def _zero_noise(value: float | None, floor: float) -> float | None:
    """Interpolation round-off across bonded faces (~1e-16) is reported as an exact zero."""
    return 0.0 if value is not None and abs(value) < floor else value


T_FLOOR_K = 1e-9
V_FLOOR_V = 1e-12


def evaluate_profile(ds: NodalDataset, req: ProfileRequest, expected: ContactProfile) -> LayerProfile:
    """Layer-by-layer temperature profile, passive/active split and effective contact conductances."""
    stack = req.stack or ds.proposed_stack
    if stack is None:
        raise ValueError("no layer stack: define the layers (hot → cold) first")
    unknown = sorted({b for layer in stack.layers for b in layer.bodies} - set(ds.bodies))
    if unknown:
        raise ValueError(f"unknown bodies in the stack: {', '.join(unknown)}")
    no_t = [b for layer in stack.layers for b in layer.bodies if "temperature_c" not in ds.bodies[b]]
    if no_t:
        raise ValueError(f"no temperature export for: {', '.join(no_t)}")

    issues: list[Issue] = []
    unused = sorted(set(ds.bodies) - {b for layer in stack.layers for b in layer.bodies})
    if unused:
        issues.append(_issue(Level.INFO, "unused-bodies", f"not in the stack: {', '.join(unused)}"))
    layers = [Layer(layer.name, tuple(layer.bodies), layer.active) for layer in stack.layers]
    frame = layer_profile(ds.bodies, layers, stack.axis, tol_mm=req.tol_mm)
    rows = [
        LayerRow(
            kind=r.kind,
            name=r.name,
            active=bool(r.active),
            t_hot_c=_none(r.t_hot_c),
            t_cold_c=_none(r.t_cold_c),
            dt_k=float(_zero_noise(r.dt_k, T_FLOOR_K)),
            area_mm2=float(r.area_mm2),
            s_hot_mm=float(r.s_hot_mm),
            s_cold_mm=float(r.s_cold_mm),
        )
        for r in frame.itertuples(index=False)
    ]
    split = None
    if frame["active"].any():
        s = passive_active_split(frame)
        split = LayerSplit(**{k: (_none(v) if k == "passive_share" else float(v)) for k, v in s.items()})
    else:
        issues.append(_issue(Level.WARNING, "no-active-layer", "mark the leg layer as active to split ΔT"))

    contacts = []
    for itf in find_interfaces(ds.bodies, layers, stack.axis, req.tol_mm):
        hot_layer, cold_layer = layers[itf.index], layers[itf.index + 1]
        at_active = hot_layer.active != cold_layer.active
        metal = ("cold" if hot_layer.active else "hot") if at_active else None
        leg = ("hot" if hot_layer.active else "cold") if at_active else None
        st = contact_state(ds.bodies[itf.hot], ds.bodies[itf.cold], stack.axis, itf.level_mm, req.tol_mm, metal, leg)
        tcc, t_verdict = _verdict(st["heat_flux_w_m2"], st["dt_k"], expected.tcc_w_m2k)
        ecc, e_verdict = (None, "not-evaluated")
        if at_active:
            ecc, e_verdict = _verdict(st["current_density_a_m2"], st["dv_v"], expected.ecc_s_m2)
        check = ContactCheck(
            interface=f"{itf.hot} | {itf.cold}",
            hot_body=itf.hot,
            cold_body=itf.cold,
            at_active_layer=at_active,
            level_mm=itf.level_mm,
            area_mm2=st["area_mm2"],
            dt_k=_none(_zero_noise(st["dt_k"], T_FLOOR_K)),
            heat_flux_w_m2=_none(st["heat_flux_w_m2"]),
            tcc_eff_w_m2k=_none(tcc),
            dv_v=_none(_zero_noise(st["dv_v"], V_FLOOR_V)),
            current_density_a_m2=_none(st["current_density_a_m2"]),
            ecc_eff_s_m2=_none(ecc),
            thermal_verdict=t_verdict,
            electrical_verdict=e_verdict,
        )
        contacts.append(check)
        if at_active:
            issues.extend(_contact_issues(check, expected))
    if not contacts:
        issues.append(_issue(Level.WARNING, "no-interfaces", f"no touching faces found within {req.tol_mm:g} mm"))
    return LayerProfile(stack=stack, rows=rows, split=split, contacts=contacts, expected=expected, issues=issues)


def _contact_issues(c: ContactCheck, expected: ContactProfile) -> list[Issue]:
    out = []
    for kind, verdict, eff, spec, unit in (
        ("thermal", c.thermal_verdict, c.tcc_eff_w_m2k, expected.tcc_w_m2k, "W/(m²·K)"),
        ("electrical", c.electrical_verdict, c.ecc_eff_s_m2, expected.ecc_s_m2, "S/m²"),
    ):
        if verdict == "bonded":
            out.append(
                _issue(
                    Level.WARNING,
                    f"contact-{kind}-bonded",
                    f"{c.interface}: no {kind} jump across the contact (effective conductance ≥ "
                    f"{1 / BONDED_FRACTION:.0f}× the specified {spec:.3g} {unit}) - the contact conductance "
                    "was not applied in this system",
                )
            )
        elif verdict == "deviates" and eff is not None:
            out.append(
                _issue(
                    Level.WARNING,
                    f"contact-{kind}-deviates",
                    f"{c.interface}: effective {kind} conductance {eff:.3g} {unit} is {eff / spec:.2f}× the "
                    f"specified {spec:.3g} {unit}",
                )
            )
    return out


# --------------------------------------------------------------------------------- view projection
def _round_sig(values: np.ndarray, digits: int = 6) -> np.ndarray:
    finite = values[np.isfinite(values)]
    scale = np.max(np.abs(finite)) if finite.size else 0.0
    if scale == 0:
        return values
    decimals = max(0, digits - 1 - int(np.floor(np.log10(scale))))
    return np.round(values, decimals)


def project_points(ds: NodalDataset, req: PointsRequest) -> NodalPoints:
    """Nodal values in the 2D coordinates of a standard view, nearest faces drawn last."""
    tags = req.bodies if req.bodies is not None else list(ds.bodies)
    unknown = sorted(set(tags) - set(ds.bodies))
    if unknown:
        raise ValueError(f"unknown bodies: {', '.join(unknown)}")
    q = req.quantity.value
    (h_ax, h_sign), (v_ax, v_sign), (d_ax, d_sign) = VIEW_AXES[req.view]
    parts, extent = [], []
    for k, tag in enumerate(tags):
        f = ds.bodies[tag]
        extent.append(f[[f"{h_ax}_mm", f"{v_ax}_mm"]].agg(["min", "max"]).to_numpy())
        if q not in f:
            continue
        f = f[f[q].notna()]
        if f.empty:
            continue
        depth = d_sign * f[f"{d_ax}_mm"].to_numpy(dtype=float)
        if req.surface_only:
            keep = depth >= depth.max() - max(1e-4, 1e-4 * (depth.max() - depth.min()))
            f, depth = f[keep], depth[keep]
        parts.append(
            pd.DataFrame(
                {
                    "h": h_sign * f[f"{h_ax}_mm"].to_numpy(dtype=float),
                    "v": v_sign * f[f"{v_ax}_mm"].to_numpy(dtype=float),
                    "value": f[q].to_numpy(dtype=float),
                    "depth": depth,
                    "body": k,
                }
            )
        )
    if not parts:
        raise ValueError(f"none of the selected bodies has {q}")
    pts = pd.concat(parts, ignore_index=True)
    n_total = len(pts)
    value_range = (float(pts["value"].min()), float(pts["value"].max()))
    if n_total > req.max_points:
        pts = pts.iloc[np.sort(np.random.default_rng(0).choice(n_total, req.max_points, replace=False))]
    pts = pts.sort_values("depth", kind="stable")
    ext = np.concatenate(extent, axis=1)  # rows: min, max; columns: (h, v) per body
    hs = h_sign * ext[:, 0::2]
    vs = v_sign * ext[:, 1::2]
    unit = CANONICAL[NODAL_DIMENSION[req.quantity]]

    def label(ax: Axis, sign: int) -> str:
        return f"{'−' if sign < 0 else ''}{ax} [mm]"

    return NodalPoints(
        view=req.view,
        quantity=req.quantity,
        unit=unit,
        h_label=label(h_ax, h_sign),
        v_label=label(v_ax, v_sign),
        h_mm=np.round(pts["h"].to_numpy(), 6).tolist(),
        v_mm=np.round(pts["v"].to_numpy(), 6).tolist(),
        value=_round_sig(pts["value"].to_numpy()).tolist(),
        body_index=pts["body"].astype(int).tolist(),
        bodies=list(tags),
        h_range_mm=(float(hs.min()), float(hs.max())),
        v_range_mm=(float(vs.min()), float(vs.max())),
        value_range=value_range,
        n_total=n_total,
        n_returned=len(pts),
    )


# -------------------------------------------------------------------------------------------- demo
@lru_cache(maxsize=4)
def demo_archive(contacts_applied: bool = True) -> bytes:
    """The synthetic unit cell as the export script would write it (zip of text files, PNGs, manifest)."""
    from .synthetic import CellParameters, demo_zip  # local import: only the demo needs the generator

    params = CellParameters() if contacts_applied else CellParameters(tcc_w_m2k=None, ecc_s_m2=None)
    return demo_zip(params)


def demo_dataset(contacts_applied: bool = True) -> NodalDataset:
    name = "synthetic-cell_" + ("contacts-applied" if contacts_applied else "bonded") + ".zip"
    return build_dataset([(name, demo_archive(contacts_applied))], synthetic=True)


# ------------------------------------------------------------------------------------ dataset store
class DatasetStore:
    """In-process LRU cache of uploaded datasets (per server process: run a single uvicorn worker)."""

    def __init__(self, max_items: int = 8, ttl_s: float = 4 * 3600):
        self.max_items = max_items
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, NodalDataset]] = OrderedDict()

    def _evict(self, now: float) -> None:
        for key in [k for k, (t, _) in self._items.items() if now - t > self.ttl_s]:
            del self._items[key]
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def put(self, ds: NodalDataset) -> None:
        with self._lock:
            now = time.monotonic()
            self._items[ds.dataset_id] = (now, ds)
            self._items.move_to_end(ds.dataset_id)
            self._evict(now)

    def get(self, dataset_id: str) -> NodalDataset:
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            if dataset_id not in self._items:
                raise KeyError(dataset_id)
            _, ds = self._items[dataset_id]
            self._items[dataset_id] = (now, ds)  # sliding expiry
            self._items.move_to_end(dataset_id)
            return ds

    def delete(self, dataset_id: str) -> bool:
        with self._lock:
            return self._items.pop(dataset_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
