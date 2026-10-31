"""Readers (adapters) that turn ANSYS exports into typed raw tables.

Each reader implements the same small protocol - ``sniff`` returns a confidence that it understands
the file, ``read`` parses it - and is registered in ``READERS``.  ``read_source`` picks the best
reader, so callers never branch on file formats (Strategy + Registry).

Supported layouts
-----------------
* **Workbench "Outline of All Parameters"** (copied to Excel/CSV): columns ``ID | Parameter Name |
  Value | Unit``; system rows such as ``Dense mesh copy C 10st (D1)`` introduce the parameters that
  belong to that analysis system (thesis Fig. 3.10).
* **Workbench "Table of Design Points"** CSV export: ``# P1 - Current Magnitude, P2 - ...`` comment
  lines, a ``Name, P1, P2, ...`` header, an optional ``Units`` row and one row per design point.
* **Mechanical result "Export Text File"** (tab separated ``.txt``; the legacy ``.xls`` export is the
  same text): ``Node Number | X Location (mm) | Y Location (mm) | Z Location (mm) | Temperature (°C)``.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from ..units import conversion, split_name_unit
from .keys import NODAL_DIMENSION, NodalKey
from .schemas import Direction, ParameterDef, SourceFormat, SystemRef

__all__ = [
    "READERS",
    "DesignPointReader",
    "NodalReader",
    "NodalTable",
    "OutlineReader",
    "RawTable",
    "decode",
    "read_source",
    "sniff",
]

PID_RE = re.compile(r"^P\d+$")
SYSTEM_ROW_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<cell>(?P<letter>[A-Z]{1,2})\d+)\)\s*$")
PDEF_RE = re.compile(r"\b(P\d+)\s*-\s*(.*?)(?=,\s*P\d+\s*-|$)")
PLACEHOLDERS = ("new input parameter", "new output parameter", "new expression", "charts", "*")


# ---------------------------------------------------------------------------------- data carriers
@dataclass
class RawTable:
    """Parameters of one Workbench export: definitions plus a (design points × parameter IDs) matrix."""

    source: str
    format: SourceFormat
    confidence: float
    parameters: list[ParameterDef]
    values: pd.DataFrame  # index: design-point name, columns: parameter IDs, float64
    systems: list[SystemRef] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class NodalTable:
    """One Mechanical nodal export in canonical units (coordinates in mm, fields as in ``NodalKey``)."""

    source: str
    frame: pd.DataFrame
    quantities: dict[NodalKey, str]
    averaged: bool
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


class Reader(Protocol):
    format: SourceFormat

    def sniff(self, text: str | None, rows: list[list[str]]) -> float: ...

    def read(self, source: str, text: str | None, rows: list[list[str]]) -> RawTable | NodalTable: ...


# -------------------------------------------------------------------------------------- utilities
def decode(data: bytes) -> str:
    """ANSYS writes UTF-8, UTF-16 with a byte-order mark, or the Windows code page (°C as 0xB0)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _detect_delimiter(lines: Sequence[str]) -> str:
    sample = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")][:30]
    counts = {d: sum(ln.count(d) for ln in sample) for d in ("\t", ";", ",")}
    return max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","


def _rows_from_text(text: str) -> list[list[str]]:
    lines = text.splitlines()
    delimiter = _detect_delimiter(lines)
    return [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delimiter)]


def _rows_from_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [["" if c is None else (repr(c) if isinstance(c, float) else str(c)).strip() for c in r] for r in ws.values]
    wb.close()
    return rows


def to_float(cell: object) -> float:
    """Parse a numeric cell; supports decimal commas and blanks (-> NaN)."""
    if cell is None:
        return np.nan
    if isinstance(cell, int | float):
        return float(cell)
    s = str(cell).strip().replace("−", "-")
    if not s:
        return np.nan
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _fold(s: str) -> str:
    return " ".join(s.split()).casefold()


# ---------------------------------------------------------------------------------- outline reader
class OutlineReader:
    """Workbench *Outline of All Parameters* (``ID | Parameter Name | Value | Unit``)."""

    format = SourceFormat.WORKBENCH_OUTLINE

    @staticmethod
    def _header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
        for i, row in enumerate(rows[:50]):
            folded = [_fold(c) for c in row]
            if "id" in folded and "parameter name" in folded:
                cols = {"id": folded.index("id"), "name": folded.index("parameter name")}
                for label in ("value", "unit"):
                    if label in folded:
                        cols[label] = folded.index(label)
                return i, cols
        return None

    def sniff(self, text: str | None, rows: list[list[str]]) -> float:
        if self._header(rows) is not None:
            return 0.95
        system_rows = sum(1 for r in rows if r and SYSTEM_ROW_RE.match(r[0] or ""))
        pid_rows = sum(1 for r in rows if r and PID_RE.match(r[0] or "") and len(r) >= 3)
        return 0.7 if system_rows and pid_rows else 0.0

    def read(self, source: str, text: str | None, rows: list[list[str]]) -> RawTable:
        found = self._header(rows)
        if found is None:
            start, cols = 0, {"id": 0, "name": 1, "value": 2, "unit": 3}
        else:
            start, cols = found[0] + 1, found[1]

        def cell(row: list[str], key: str) -> str:
            j = cols.get(key)
            return row[j].strip() if j is not None and j < len(row) else ""

        params: list[ParameterDef] = []
        values: dict[str, float] = {}
        systems: dict[str, SystemRef] = {}
        notes: list[str] = []
        direction: Direction | None = None
        current: str | None = None
        for row in rows[start:]:
            ident = cell(row, "id")
            if not ident:
                continue
            folded = _fold(ident)
            if folded in ("input parameters", "output parameters"):
                direction = Direction.INPUT if folded.startswith("input") else Direction.OUTPUT
                current = None
                continue
            if folded in PLACEHOLDERS or folded.startswith("new "):
                continue
            system = SYSTEM_ROW_RE.match(ident)
            if system:
                ref = SystemRef(letter=system["letter"], title=" ".join(system["title"].split()), cell=system["cell"])
                systems.setdefault(ref.letter, ref)
                current = ref.letter
                continue
            if PID_RE.match(ident):
                unit = cell(row, "unit") or None
                params.append(
                    ParameterDef(pid=ident, name=cell(row, "name"), unit=unit, system=current, direction=direction)
                )
                values[ident] = to_float(cell(row, "value"))
                continue
            notes.append(f"row '{ident}' not recognized; skipped")
        frame = pd.DataFrame([values], index=pd.Index(["Current"], name="design_point"), dtype="float64")
        if systems:
            notes.append(
                f"{len(systems)} analysis system(s): " + ", ".join(f"{s.letter} = {s.title}" for s in systems.values())
            )
        return RawTable(source, self.format, 0.95, params, frame, list(systems.values()), notes)


# ----------------------------------------------------------------------------- design-point reader
class DesignPointReader:
    """Workbench *Table of Design Points* CSV (``Export Table Data as CSV``)."""

    format = SourceFormat.WORKBENCH_DESIGN_POINTS

    @staticmethod
    def _header_index(rows: list[list[str]]) -> int | None:
        for i, row in enumerate(rows[:200]):
            if row and _fold(row[0]) == "name" and any(PID_RE.match(c) for c in row[1:]):
                return i
        return None

    def sniff(self, text: str | None, rows: list[list[str]]) -> float:
        if self._header_index(rows) is None:
            return 0.0
        has_defs = text is not None and "parameters defined in the project" in text.casefold()
        return 0.95 if has_defs else 0.8

    def read(self, source: str, text: str | None, rows: list[list[str]]) -> RawTable:
        definitions: dict[str, str] = {}
        for line in (text or "").splitlines():
            if line.lstrip().startswith("#"):
                for pid, name in PDEF_RE.findall(line.lstrip("# ")):
                    definitions[pid] = name.strip()
        h = self._header_index(rows)
        if h is None:
            raise ValueError(f"{source}: no 'Name, P1, P2, ...' header row")
        header = rows[h]
        pid_cols = {j: c for j, c in enumerate(header) if PID_RE.match(c)}
        units: dict[str, str] = {}
        data_start = h + 1
        if data_start < len(rows) and rows[data_start] and _fold(rows[data_start][0]) == "units":
            units = {pid_cols[j]: rows[data_start][j] for j in pid_cols if j < len(rows[data_start])}
            data_start += 1
        names, matrix = [], []
        for row in rows[data_start:]:
            if not row or not row[0] or row[0].lstrip().startswith("#"):
                continue
            names.append(row[0])
            matrix.append([to_float(row[j]) if j < len(row) else np.nan for j in pid_cols])
        params = [
            ParameterDef(pid=pid, name=definitions.get(pid, pid), unit=units.get(pid) or None)
            for pid in pid_cols.values()
        ]
        frame = pd.DataFrame(
            np.asarray(matrix, dtype="float64").reshape(len(names), len(pid_cols)),
            index=pd.Index(names, name="design_point"),
            columns=list(pid_cols.values()),
        )
        notes = [f"{len(names)} design point(s), {len(params)} parameter(s)"]
        missing = [pid for pid in pid_cols.values() if pid not in definitions]
        if missing:
            notes.append("no name found for " + ", ".join(missing))
        extra = [c for j, c in enumerate(header) if j and j not in pid_cols and c]
        if extra:
            notes.append("ignored columns: " + ", ".join(extra))
        return RawTable(source, self.format, self.sniff(text, rows), params, frame, [], notes)


# ------------------------------------------------------------------------------------ nodal reader
NODAL_NAMES: dict[str, NodalKey] = {
    "temperature": NodalKey.TEMPERATURE,
    "electric voltage": NodalKey.VOLTAGE,
    "total current density": NodalKey.CURRENT_DENSITY,
    "joule heat": NodalKey.JOULE_HEAT,
    "total heat flux": NodalKey.HEAT_FLUX,
}


class NodalReader:
    """Mechanical result *Export Text File*: one row per node (or per element node when unaveraged)."""

    format = SourceFormat.MECHANICAL_NODAL

    @staticmethod
    def _header_index(rows: list[list[str]]) -> int | None:
        for i, row in enumerate(rows[:20]):
            folded = [_fold(c) for c in row]
            if any(c in ("node number", "node") for c in folded) and any("location" in c for c in folded):
                return i
        return None

    def sniff(self, text: str | None, rows: list[list[str]]) -> float:
        return 0.95 if self._header_index(rows) is not None else 0.0

    def read(self, source: str, text: str | None, rows: list[list[str]]) -> NodalTable:
        if text is None:
            raise ValueError(f"{source}: nodal exports are text files")
        h = self._header_index(rows)
        if h is None:
            raise ValueError(f"{source}: no 'Node Number ... Location' header")
        delimiter = _detect_delimiter(text.splitlines())
        raw = pd.read_csv(io.StringIO(text), sep=delimiter, skiprows=h, dtype=str, skipinitialspace=True)
        raw.columns = [" ".join(str(c).split()) for c in raw.columns]
        out = pd.DataFrame(index=raw.index)
        quantities: dict[NodalKey, str] = {}
        notes: list[str] = []
        for col in raw.columns:
            name, unit = split_name_unit(col)
            folded = _fold(name)
            numeric = pd.to_numeric(raw[col].str.replace(",", ".", regex=False), errors="coerce")
            if folded in ("node number", "node"):
                out["node_id"] = numeric.astype("Int64")
            elif folded == "element number":
                out["element_id"] = numeric.astype("Int64")
            elif folded in ("x location", "y location", "z location"):
                factor, offset, note = conversion(unit, "length")
                out[f"{folded[0]}_mm"] = numeric * factor + offset
                if note:
                    notes.append(f"{col}: {note}")
            elif folded in NODAL_NAMES:
                key = NODAL_NAMES[folded]
                factor, offset, note = conversion(unit, NODAL_DIMENSION[key])
                out[key.value] = numeric * factor + offset
                quantities[key] = col
                if note:
                    notes.append(f"{col}: {note}")
            else:
                notes.append(f"column '{col}' not recognized; skipped")
        required = {"node_id", "x_mm", "y_mm", "z_mm"}
        if not required <= set(out.columns):
            raise ValueError(f"{source}: missing {sorted(required - set(out.columns))}")
        out = out.dropna(subset=["x_mm", "y_mm", "z_mm"]).reset_index(drop=True)
        averaged = bool(out["node_id"].is_unique)
        if not averaged:
            notes.append("repeated node numbers: unaveraged (element-nodal) values")
        return NodalTable(source, out, quantities, averaged, 0.95, notes)


READERS: tuple[Reader, ...] = (OutlineReader(), DesignPointReader(), NodalReader())


# -------------------------------------------------------------------------------------- entry points
def _load(data: bytes, source: str) -> tuple[str | None, list[list[str]]]:
    if data[:2] == b"PK":  # xlsx is a zip container; Mechanical's legacy ".xls" export is plain text
        return None, _rows_from_xlsx(data)
    text = decode(data)
    return text, _rows_from_text(text)


def sniff(data: bytes, source: str) -> list[tuple[SourceFormat, float]]:
    text, rows = _load(data, source)
    return sorted(((r.format, r.sniff(text, rows)) for r in READERS), key=lambda t: -t[1])


def read_source(data: bytes, source: str) -> RawTable | NodalTable:
    """Parse one file with the reader that recognizes it best."""
    text, rows = _load(data, source)
    scored = sorted(((r.sniff(text, rows), i) for i, r in enumerate(READERS)), reverse=True)
    confidence, index = scored[0]
    if confidence < 0.5:
        raise ValueError(f"{source}: not a recognized ANSYS export (outline, design points or nodal text)")
    result = READERS[index].read(source, text, rows)
    result.confidence = confidence
    return result
