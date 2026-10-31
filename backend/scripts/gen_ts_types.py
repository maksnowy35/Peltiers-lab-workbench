"""Mirror the Pydantic contract of ``/api/pipeline`` to TypeScript.

    python backend/scripts/gen_ts_types.py          # (re)write frontend/src/app/core/pipeline.models.ts
    python backend/scripts/gen_ts_types.py --check  # exit 1 if the file is stale (CI and a test run this)

Rules
-----
* Response models: every field is present (the server serializes defaults), ``X | None`` → ``X | null``.
* Request models: fields with a default are optional (``name?:``) - the server fills them in.
* A model used in both directions and having defaults gets two interfaces: ``Model`` (response shape)
  and ``ModelInput`` (request shape); a response value is always assignable to the input type.
* Enums and named ``Literal`` aliases become string-literal unions plus a ``*_VALUES`` array.
"""

from __future__ import annotations

import argparse
import collections.abc
import enum
import re
import sys
import types
import typing
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from teglab.pipeline import schemas as s  # noqa: E402

TARGET = ROOT / "frontend" / "src" / "app" / "core" / "pipeline.models.ts"
RESPONSES: list[type[BaseModel]] = [
    s.PipelineReport,
    s.ProjectProfile,
    s.NodalDatasetInfo,
    s.LayerProfile,
    s.NodalPoints,
    s.OverlayPlacement,
    s.ExampleInfo,
    s.DeleteResult,
]
REQUESTS: list[type[BaseModel]] = [s.RunSettings, s.ProfileRequest, s.PointsRequest, s.ImageCalibration]
ALIASES: dict[str, object] = {"Axis": s.Axis, "View": s.View, "ContactVerdict": s.ContactVerdict}

Mode = typing.Literal["output", "input"]


def _snake_upper(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def _literal(value: object) -> str:
    return "'" + str(value).replace("'", "\\'") + "'" if isinstance(value, str) else str(value).lower()


class Generator:
    def __init__(self) -> None:
        self.modes: dict[type[BaseModel], set[str]] = {}
        self.enums: dict[str, list[str]] = {}
        self.used_aliases: set[str] = set()
        for model in RESPONSES:
            self._visit(model, "output")
        for model in REQUESTS:
            self._visit(model, "input")

    # ----------------------------------------------------------------------------------- traversal
    def _visit(self, model: type[BaseModel], mode: Mode) -> None:
        seen = self.modes.setdefault(model, set())
        if mode in seen:
            return
        seen.add(mode)
        for f in model.model_fields.values():
            for inner in self._models_in(f.annotation):
                self._visit(inner, mode)

    def _models_in(self, tp: object) -> list[type[BaseModel]]:
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            return [tp]
        return [m for arg in typing.get_args(tp) for m in self._models_in(arg)]

    def _dual(self, model: type[BaseModel]) -> bool:
        """Used in both directions and shaped differently: own defaults, or a nested model that is dual."""
        if self.modes.get(model, set()) != {"input", "output"}:
            return False
        fields = model.model_fields.values()
        if any(not f.is_required() for f in fields):
            return True
        return any(self._dual(inner) for f in fields for inner in self._models_in(f.annotation) if inner is not model)

    def name(self, model: type[BaseModel], mode: Mode) -> str:
        return model.__name__ + ("Input" if mode == "input" and self._dual(model) else "")

    # -------------------------------------------------------------------------------------- types
    def ts(self, tp: object, mode: Mode) -> str:
        for alias, target in ALIASES.items():
            if tp == target:
                self.used_aliases.add(alias)
                return alias
        origin, args = typing.get_origin(tp), typing.get_args(tp)
        if tp is type(None):
            return "null"
        if tp is str:
            return "string"
        if tp in (int, float):
            return "number"
        if tp is bool:
            return "boolean"
        if isinstance(tp, type) and issubclass(tp, enum.Enum):
            self.enums[tp.__name__] = [m.value for m in tp]
            return tp.__name__
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            return self.name(tp, mode)
        if origin is typing.Literal:
            return " | ".join(_literal(a) for a in args)
        if origin in (typing.Union, types.UnionType):
            parts = [self.ts(a, mode) for a in args]
            nulls = [p for p in parts if p == "null"]
            return " | ".join(dict.fromkeys([p for p in parts if p != "null"] + nulls))
        if origin in (list, set, frozenset, collections.abc.Sequence):
            inner = self.ts(args[0], mode)
            return f"({inner})[]" if " | " in inner else f"{inner}[]"
        if origin is tuple:
            if len(args) == 2 and args[1] is Ellipsis:
                return f"{self.ts(args[0], mode)}[]"
            return "[" + ", ".join(self.ts(a, mode) for a in args) + "]"
        if origin is dict:
            key, value = args
            v = self.ts(value, mode)
            if isinstance(key, type) and issubclass(key, enum.Enum):
                return f"Partial<Record<{self.ts(key, mode)}, {v}>>"
            return f"Record<string, {v}>"
        if origin is typing.Annotated:
            return self.ts(args[0], mode)
        raise TypeError(f"no TypeScript mapping for {tp!r}")

    # ------------------------------------------------------------------------------------ emitting
    def interface(self, model: type[BaseModel], mode: Mode) -> str:
        lines = []
        doc = (model.__doc__ or "").strip().split("\n\n")[0]
        if doc and model.__module__.startswith("teglab"):
            lines.append("/** " + " ".join(doc.split()).replace("*/", "* /") + " */")
        lines.append(f"export interface {self.name(model, mode)} {{")
        for fname, f in model.model_fields.items():
            if f.description:
                lines.append("  /** " + " ".join(f.description.split()).replace("*/", "* /") + " */")
            optional = "?" if mode == "input" and not f.is_required() else ""
            lines.append(f"  {fname}{optional}: {self.ts(f.annotation, mode)};")
        lines.append("}")
        return "\n".join(lines)

    def render(self) -> str:
        blocks = []
        for model in sorted(self.modes, key=lambda m: m.__name__):
            modes = self.modes[model]
            if self._dual(model):
                blocks.append(self.interface(model, "output"))
                blocks.append(self.interface(model, "input"))
            else:
                blocks.append(self.interface(model, "output" if "output" in modes else "input"))
        head = [
            "// Generated by backend/scripts/gen_ts_types.py from teglab.pipeline.schemas - do not edit by hand.",
            "// Regenerate after changing a model: python backend/scripts/gen_ts_types.py",
            "/* eslint-disable */",
            "",
        ]
        unions = {name: list(typing.get_args(ALIASES[name])) for name in self.used_aliases} | self.enums
        for name in sorted(unions):  # blocks are rendered first: that is what collects the unions
            literals = [_literal(v) for v in unions[name]]
            head.append(f"export type {name} = {' | '.join(literals)};")
            head.append(f"export const {_snake_upper(name)}_VALUES: readonly {name}[] = [{', '.join(literals)}];")
        return "\n".join(head) + "\n\n" + "\n\n".join(blocks) + "\n"


def render() -> str:
    return Generator().render()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the generated file is out of date")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is stale: run python backend/scripts/gen_ts_types.py", file=sys.stderr)
            return 1
        print("TypeScript contract is up to date")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
