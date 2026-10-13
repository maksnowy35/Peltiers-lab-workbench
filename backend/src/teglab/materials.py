"""Thermoelectric material models and the built-in material library.

Every material is described by temperature-dependent tables of the Seebeck
coefficient alpha(T) [V/K], electrical resistivity rho(T) [Ohm*m] and thermal
conductivity kappa(T) [W/(m*K)].  Values between table points are interpolated
with a shape-preserving PCHIP spline, which keeps d(alpha)/dT continuous; this
matters because the Thomson coefficient tau = T*d(alpha)/dT enters the energy
balance and a kinked alpha(T) would create artificial local heat sources.
Outside the tabulated range the properties are held constant (no
extrapolation) and the solver reports how much of the leg ran out of range.

The library curves are *representative* values for each material class,
chosen to reproduce typical literature magnitudes (peak zT, alpha, kappa).
They are meant for screening and teaching.  Replace them with measured data
(CSV upload in the UI or ``Material.from_csv``) before drawing conclusions.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import PchipInterpolator

__all__ = ["Material", "MaterialLibrary", "default_library"]


@dataclass
class Material:
    id: str
    name: str
    kind: str  # "p" or "n"
    family: str
    t_k: np.ndarray
    alpha: np.ndarray  # V/K (positive for p-type, negative for n-type)
    rho: np.ndarray  # Ohm*m
    kappa: np.ndarray  # W/(m*K)
    t_limit_k: float  # recommended maximum operating temperature
    density_g_cm3: float
    notes: str = ""
    source: str = "representative"
    _interp: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.t_k = np.atleast_1d(np.asarray(self.t_k, dtype=float))
        self.alpha = np.atleast_1d(np.asarray(self.alpha, dtype=float))
        self.rho = np.atleast_1d(np.asarray(self.rho, dtype=float))
        self.kappa = np.atleast_1d(np.asarray(self.kappa, dtype=float))
        n = self.t_k.size
        if not (self.alpha.size == self.rho.size == self.kappa.size == n):
            raise ValueError(f"material {self.id}: property tables must have equal length")
        if n > 1:
            order = np.argsort(self.t_k)
            self.t_k = self.t_k[order]
            self.alpha, self.rho, self.kappa = self.alpha[order], self.rho[order], self.kappa[order]
            if np.any(np.diff(self.t_k) <= 0):
                raise ValueError(f"material {self.id}: temperatures must be strictly increasing")
            spline = PchipInterpolator(self.t_k, np.column_stack([self.alpha, self.rho, self.kappa]), extrapolate=False)
            self._interp = {
                "alpha": PchipInterpolator(self.t_k, self.alpha, extrapolate=False),
                # piecewise-cubic coefficients of all three properties, evaluated directly (fast path)
                "x": np.asarray(spline.x, dtype=float),
                "c": np.asarray(spline.c, dtype=float),  # shape (4, n_intervals, 3)
            }
        if np.any(self.rho <= 0) or np.any(self.kappa <= 0):
            raise ValueError(f"material {self.id}: rho and kappa must be positive")

    # ------------------------------------------------------------------ props
    @property
    def is_constant(self) -> bool:
        return self.t_k.size == 1

    @property
    def t_min(self) -> float:
        return float(self.t_k[0])

    @property
    def t_max(self) -> float:
        return float(self.t_k[-1])

    def _clip(self, t: np.ndarray) -> np.ndarray:
        return np.clip(t, self.t_min, self.t_max)

    def props(self, t) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (alpha, rho, kappa) evaluated at temperature(s) ``t`` [K]."""
        t = np.asarray(t, dtype=float)
        if self.is_constant:
            shape = t.shape
            return (
                np.full(shape, self.alpha[0]),
                np.full(shape, self.rho[0]),
                np.full(shape, self.kappa[0]),
            )
        x, c = self._interp["x"], self._interp["c"]
        tc = np.minimum(np.maximum(t, x[0]), x[-1])
        i = np.minimum(np.searchsorted(x, tc, side="right") - 1, x.size - 2)
        i = np.maximum(i, 0)
        dx = (tc - x[i])[..., None]
        v = ((c[0, i] * dx + c[1, i]) * dx + c[2, i]) * dx + c[3, i]
        return v[..., 0], v[..., 1], v[..., 2]

    def dalpha_dt(self, t) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        if self.is_constant:
            return np.zeros_like(t)
        inside = (t >= self.t_min) & (t <= self.t_max)
        d = self._interp["alpha"].derivative()(self._clip(t))
        return np.where(inside, d, 0.0)

    def zt(self, t) -> np.ndarray:
        a, r, k = self.props(t)
        return a * a * np.asarray(t, dtype=float) / (r * k)

    def compatibility(self, t) -> np.ndarray:
        """Compatibility factor s = (sqrt(1+zT)-1)/(alpha*T) [1/V] (Snyder & Ursell 2003)."""
        t = np.asarray(t, dtype=float)
        a, _, _ = self.props(t)
        zt = self.zt(t)
        return (np.sqrt(1.0 + np.maximum(zt, 0.0)) - 1.0) / (np.abs(a) * t)

    def seebeck_integral(self, t_cold: float, t_hot: float, n: int = 64) -> float:
        """Integral of alpha(T) dT from t_cold to t_hot [V]."""
        if self.is_constant:
            return float(self.alpha[0] * (t_hot - t_cold))
        ts = np.linspace(t_cold, t_hot, n)
        return float(np.trapezoid(self.props(ts)[0], ts))

    def with_constant_alpha(self, alpha: float, suffix: str = "const-alpha") -> Material:
        """Copy with alpha fixed (rho, kappa unchanged). Used to switch the Thomson term off."""
        return Material(
            id=f"{self.id}:{suffix}",
            name=f"{self.name} ({suffix})",
            kind=self.kind,
            family=self.family,
            t_k=self.t_k.copy(),
            alpha=np.full_like(self.t_k, alpha),
            rho=self.rho.copy(),
            kappa=self.kappa.copy(),
            t_limit_k=self.t_limit_k,
            density_g_cm3=self.density_g_cm3,
            notes=self.notes,
            source=self.source,
        )

    # ------------------------------------------------------------- I/O
    def to_dict(self, n_points: int = 40) -> dict:
        if self.is_constant:
            t = np.array([self.t_min])
        else:
            t = np.linspace(self.t_min, self.t_max, n_points)
        a, r, k = self.props(t)
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "family": self.family,
            "t_min_c": self.t_min - 273.15,
            "t_max_c": self.t_max - 273.15,
            "t_limit_c": self.t_limit_k - 273.15,
            "density_g_cm3": self.density_g_cm3,
            "constant": self.is_constant,
            "notes": self.notes,
            "source": self.source,
            "zt_peak": float(np.max(self.zt(t))),
            "curve": {
                "t_c": (t - 273.15).tolist(),
                "alpha_uv_k": (a * 1e6).tolist(),
                "rho_uohm_m": (r * 1e6).tolist(),
                "kappa": k.tolist(),
                "zt": self.zt(t).tolist(),
                "s_per_v": self.compatibility(t).tolist(),
            },
        }

    @classmethod
    def from_csv(
        cls,
        text: str,
        *,
        id: str,
        name: str,
        kind: str,
        family: str = "custom",
        t_limit_c: float | None = None,
        density_g_cm3: float = 7.0,
    ) -> Material:
        """Parse ``T, alpha, rho, kappa`` columns.

        Accepted header spellings (case-insensitive): ``T_C``/``T_K``/``T``,
        ``alpha_uV_K``/``alpha_V_K``, ``rho_uOhm_m``/``rho_Ohm_m``/``sigma_S_m``,
        ``kappa``.  A plain ``T`` column is taken as degrees Celsius.
        """
        dialect_delim = ";" if text.count(";") > text.count(",") else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=dialect_delim))
        rows = [r for r in rows if r and not r[0].strip().startswith("#")]
        if len(rows) < 2:
            raise ValueError("material CSV needs a header and at least one data row")
        header = [h.strip().lower().replace(" ", "") for h in rows[0]]

        def col(*names: str) -> int | None:
            for n in names:
                if n in header:
                    return header.index(n)
            return None

        def num(s: str) -> float:
            s = s.strip()
            if dialect_delim == ";":
                s = s.replace(",", ".")
            return float(s)

        data = np.array([[num(c) for c in r] for r in rows[1:] if any(c.strip() for c in r)])
        it_c, it_k, it = col("t_c", "t[c]", "tc"), col("t_k", "t[k]", "tk"), col("t", "temperature")
        if it_k is not None:
            t = data[:, it_k]
        elif it_c is not None:
            t = data[:, it_c] + 273.15
        elif it is not None:
            t = data[:, it] + 273.15
        else:
            raise ValueError("material CSV: temperature column (T_C or T_K) not found")
        ia_u, ia = col("alpha_uv_k", "alpha[uv/k]", "seebeck_uv_k"), col("alpha_v_k", "alpha")
        if ia_u is not None:
            alpha = data[:, ia_u] * 1e-6
        elif ia is not None:
            alpha = data[:, ia]
            if np.max(np.abs(alpha)) > 0.01:  # clearly given in uV/K
                alpha = alpha * 1e-6
        else:
            raise ValueError("material CSV: alpha column not found")
        ir_u, ir, isg = col("rho_uohm_m", "rho[uohm*m]"), col("rho_ohm_m", "rho"), col("sigma_s_m", "sigma")
        if ir_u is not None:
            rho = data[:, ir_u] * 1e-6
        elif ir is not None:
            rho = data[:, ir]
        elif isg is not None:
            rho = 1.0 / data[:, isg]
        else:
            raise ValueError("material CSV: rho or sigma column not found")
        ik = col("kappa", "kappa_w_mk", "k")
        if ik is None:
            raise ValueError("material CSV: kappa column not found")
        kappa = data[:, ik]
        if kind == "n" and np.mean(alpha) > 0:
            alpha = -alpha
        return cls(
            id=id,
            name=name,
            kind=kind,
            family=family,
            t_k=t,
            alpha=alpha,
            rho=rho,
            kappa=kappa,
            t_limit_k=(t_limit_c + 273.15) if t_limit_c is not None else float(np.max(t)),
            density_g_cm3=density_g_cm3,
            source="user CSV",
        )


def _tab(
    id: str,
    name: str,
    kind: str,
    family: str,
    t_k: list[float],
    alpha_uv: list[float],
    rho_uohm: list[float],
    kappa: list[float],
    t_limit_c: float,
    density: float,
    notes: str,
    source: str = "representative",
) -> Material:
    return Material(
        id=id,
        name=name,
        kind=kind,
        family=family,
        t_k=np.array(t_k, dtype=float),
        alpha=np.array(alpha_uv, dtype=float) * 1e-6,
        rho=np.array(rho_uohm, dtype=float) * 1e-6,
        kappa=np.array(kappa, dtype=float),
        t_limit_k=t_limit_c + 273.15,
        density_g_cm3=density,
        notes=notes,
        source=source,
    )


class MaterialLibrary:
    def __init__(self, materials: list[Material] | None = None) -> None:
        self._m: dict[str, Material] = {}
        for m in materials or []:
            self.add(m)

    def add(self, m: Material) -> None:
        self._m[m.id] = m

    def remove(self, id: str) -> None:
        self._m.pop(id, None)

    def get(self, id: str) -> Material:
        try:
            return self._m[id]
        except KeyError as exc:
            raise KeyError(f"unknown material '{id}'") from exc

    def __contains__(self, id: str) -> bool:
        return id in self._m

    def all(self) -> list[Material]:
        return list(self._m.values())

    def copy(self) -> MaterialLibrary:
        return MaterialLibrary(self.all())


_T_BITE = [300, 350, 400, 450, 500]
_T_MID = [300, 400, 500, 600, 700, 800, 850]
_T_HH_N = [300, 400, 500, 600, 700, 800, 900, 1000]
_T_HH_P = [300, 400, 500, 600, 700, 800, 900, 1000, 1100]
_T_SIGE = [300, 500, 700, 900, 1100, 1200]


def default_library() -> MaterialLibrary:
    """Built-in library. All non-thesis curves are representative approximations."""
    mats = [
        # --- thesis reference (Table 3.2): constant, isotropic properties
        _tab(
            "thesis_p",
            "Bi2Te3 p-type (thesis, constant)",
            "p",
            "Bi2Te3",
            [300],
            [200.0],
            [10.0],
            [1.5],
            250,
            6.8,
            "Constant properties from thesis Table 3.2 (α = +200 µV/K, ρ = 10 µΩ·m, κ = 1.5 W/(m·K)).",
            source="thesis Table 3.2",
        ),
        _tab(
            "thesis_n",
            "Bi2Te3 n-type (thesis, constant)",
            "n",
            "Bi2Te3",
            [300],
            [-200.0],
            [10.0],
            [1.5],
            250,
            7.7,
            "Constant properties from thesis Table 3.2 (α = −200 µV/K, ρ = 10 µΩ·m, κ = 1.5 W/(m·K)).",
            source="thesis Table 3.2",
        ),
        # --- near-room-temperature, T-dependent
        _tab(
            "bi2te3_p",
            "Bi0.5Sb1.5Te3 (p)",
            "p",
            "Bi2Te3",
            _T_BITE,
            [205, 215, 215, 205, 190],
            [10.0, 11.5, 13.0, 14.5, 16.0],
            [1.40, 1.35, 1.40, 1.55, 1.75],
            250,
            6.8,
            "Commercial-grade zone-melted BiSbTe; bipolar degradation above ~150 °C.",
        ),
        _tab(
            "bi2te3_n",
            "Bi2Te2.7Se0.3 (n)",
            "n",
            "Bi2Te3",
            _T_BITE,
            [-190, -200, -200, -190, -175],
            [10.0, 11.5, 13.0, 14.0, 15.0],
            [1.45, 1.40, 1.45, 1.60, 1.80],
            250,
            7.7,
            "Commercial-grade BiTeSe; anisotropic in practice (values along the pressing axis).",
        ),
        _tab(
            "mgagsb_p",
            "α-MgAgSb (p, Te-free)",
            "p",
            "MgAgSb",
            [300, 350, 400, 450, 500, 550],
            [185, 200, 210, 215, 215, 210],
            [18, 20, 22, 24, 26, 28],
            [0.75, 0.73, 0.72, 0.72, 0.74, 0.78],
            300,
            6.3,
            "Tellurium-free alternative to BiSbTe for 25–300 °C.",
        ),
        _tab(
            "mg3sbbi_n",
            "Mg3(Sb,Bi)2 (n, Te-free)",
            "n",
            "Mg3Sb2",
            [300, 400, 500, 600, 700],
            [-180, -200, -215, -225, -230],
            [14, 18, 23, 28, 32],
            [1.05, 0.95, 0.88, 0.84, 0.83],
            450,
            4.8,
            "Tellurium-free n-type Zintl compound; Mg loss above ~450 °C.",
        ),
        # --- mid temperature
        _tab(
            "pbte_n",
            "PbTe:I (n)",
            "n",
            "PbTe",
            _T_MID,
            [-110, -145, -180, -210, -235, -250, -255],
            [6, 9, 13, 17, 22, 27, 30],
            [2.2, 1.9, 1.6, 1.4, 1.25, 1.15, 1.12],
            550,
            8.2,
            "Lead chalcogenide; sublimation and Pb toxicity require encapsulation.",
        ),
        _tab(
            "pbte_p",
            "PbTe:Na (p)",
            "p",
            "PbTe",
            _T_MID,
            [90, 130, 170, 205, 235, 255, 262],
            [7, 10, 14, 19, 24, 29, 32],
            [2.0, 1.7, 1.45, 1.25, 1.12, 1.05, 1.02],
            550,
            8.2,
            "Na-doped PbTe (nanostructured variants reach higher zT).",
        ),
        _tab(
            "skd_n",
            "Yb-filled CoSb3 skutterudite (n)",
            "n",
            "Skutterudite",
            _T_MID,
            [-130, -150, -170, -185, -195, -200, -200],
            [5.0, 6.0, 7.2, 8.4, 9.6, 10.8, 11.4],
            [3.4, 3.2, 3.0, 2.9, 2.85, 2.85, 2.9],
            550,
            7.6,
            "Sb sublimation above ~550 °C requires coating.",
        ),
        _tab(
            "skd_p",
            "Ce/Fe-based skutterudite (p)",
            "p",
            "Skutterudite",
            _T_MID,
            [110, 135, 155, 170, 180, 185, 185],
            [8.0, 9.5, 11.0, 12.5, 14.0, 15.5, 16.3],
            [2.6, 2.4, 2.3, 2.25, 2.25, 2.3, 2.35],
            550,
            7.6,
            "p-type skutterudites lag n-type in zT.",
        ),
        # --- high temperature
        _tab(
            "hh_n",
            "(Zr,Hf)NiSn half-Heusler (n)",
            "n",
            "Half-Heusler",
            _T_HH_N,
            [-140, -165, -185, -200, -210, -215, -215, -210],
            [8, 9, 10, 11, 12, 13, 14, 15],
            [4.5, 4.2, 4.0, 3.8, 3.7, 3.6, 3.6, 3.7],
            750,
            8.4,
            "Mechanically robust, good for thermal cycling.",
        ),
        _tab(
            "hh_p",
            "NbFeSb half-Heusler (p)",
            "p",
            "Half-Heusler",
            _T_HH_P,
            [110, 135, 155, 172, 185, 195, 203, 208, 210],
            [6, 7, 8, 9, 10, 11, 12, 13, 14],
            [6.0, 5.4, 4.9, 4.5, 4.2, 4.0, 3.85, 3.75, 3.7],
            800,
            8.6,
            "High power factor; kappa is the main limitation.",
        ),
        _tab(
            "sige_n",
            "Si80Ge20:P (n)",
            "n",
            "SiGe",
            _T_SIGE,
            [-120, -170, -215, -250, -275, -282],
            [10, 14, 19, 24, 29, 31],
            [4.4, 4.1, 3.9, 3.8, 3.8, 3.9],
            950,
            3.0,
            "Radioisotope-generator heritage; needs very hot sources.",
        ),
        _tab(
            "sige_p",
            "Si80Ge20:B (p)",
            "p",
            "SiGe",
            _T_SIGE,
            [110, 155, 195, 225, 245, 250],
            [12, 17, 23, 29, 35, 38],
            [4.8, 4.5, 4.3, 4.2, 4.2, 4.3],
            950,
            3.0,
            "p-SiGe has noticeably lower zT than n-SiGe.",
        ),
    ]
    return MaterialLibrary(mats)
