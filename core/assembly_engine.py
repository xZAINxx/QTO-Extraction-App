"""Assembly engine — zero-API description composition from YAML templates.

An *assembly* is a parameterised QTO row. The user picks one from the
palette, fills in inputs (thickness, location, detail-ref…), and the
engine renders the description string locally using ``str.format``-style
substitution. This bypasses Sonnet entirely — a Phase-3 takeoff that
uses only assemblies costs $0 in API tokens.

YAML schema
-----------

Each file in ``assemblies/`` follows::

    key: cast_stone_coping_standard
    name: "Cast Stone Coping (Standard 1'-3\\" T)"
    trade: masonry
    csi_division: "DIVISION 04"
    units: LF
    description_template: |
      REMOVE & REPLACE W/ NEW (1'-3" T) CAST STONE COPING ... @ {location} AS PER {detail_ref} WHICH INCLUDES
      -1/2" DIA. S.S. 8" FISTAIL ANCHORS
      -#4 @ 12" EPOXY COATED REBARS (2 MIN.)
    inputs:
      - name: location
        label: "Location"
        type: text
        default: "PARAPET"
      - name: detail_ref
        label: "Detail / Legend ref"
        type: text
        default: "LEGEND/A106 & DETAIL 4/A401"

The optional ``math_trail_template`` (e.g. ``"({length}' L X {height}' H = {area} SQFT)"``)
is appended when both length and height are provided; the engine
auto-computes the area.

Run ``python -m core.assembly_engine`` to validate every YAML on disk.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from core.qto_row import QTORow


_LOG = logging.getLogger(__name__)
_ASSEMBLIES_DIR = Path(__file__).resolve().parent.parent / "assemblies"

_NEEDS_SUB = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass
class AssemblyInput:
    name: str
    label: str
    type: str = "text"          # text | number | select
    default: Any = ""
    options: list[str] = field(default_factory=list)
    units: str = ""             # purely informational for the UI

    @classmethod
    def from_dict(cls, data: dict) -> "AssemblyInput":
        return cls(
            name=str(data["name"]),
            label=str(data.get("label") or data["name"]),
            type=str(data.get("type", "text")),
            default=data.get("default", ""),
            options=list(data.get("options") or []),
            units=str(data.get("units", "")),
        )


@dataclass
class Assembly:
    key: str
    name: str
    trade: str
    csi_division: str
    units: str
    description_template: str
    inputs: list[AssemblyInput] = field(default_factory=list)
    math_trail_template: str = ""
    notes: str = ""
    source_file: Optional[Path] = None

    # Inputs the template still refers to that haven't been provided.
    def required_input_names(self) -> set[str]:
        return set(_NEEDS_SUB.findall(self.description_template))

    def render_description(self, values: dict[str, Any]) -> str:
        """Apply input substitutions; tolerate missing inputs by leaving blanks."""
        rendered = self.description_template
        for key, val in values.items():
            rendered = rendered.replace("{" + key + "}", str(val))
        # Strip any leftover unresolved tokens to avoid leaking braces.
        rendered = _NEEDS_SUB.sub("", rendered)
        # Collapse runs of spaces/blank lines that may result from blank inputs.
        rendered = re.sub(r"[ \t]{2,}", " ", rendered)
        return rendered.strip()

    def render_math_trail(self, values: dict[str, Any]) -> str:
        if not self.math_trail_template:
            return ""
        v = dict(values)
        # Auto-compute area when length & height provided.
        try:
            if "length" in v and "height" in v and "area" not in v:
                v["area"] = int(round(float(v["length"]) * float(v["height"])))
        except (TypeError, ValueError):
            pass
        try:
            return self.math_trail_template.format(**v)
        except KeyError:
            return ""

    def apply(
        self,
        values: dict[str, Any],
        *,
        sheet: str = "",
        details: str = "",
        qty: Optional[float] = None,
    ) -> QTORow:
        """Build a fully-populated :class:`QTORow` with no API calls."""
        all_values = {inp.name: inp.default for inp in self.inputs}
        all_values.update({k: v for k, v in (values or {}).items() if v not in (None, "")})

        desc = self.render_description(all_values)
        trail = self.render_math_trail(all_values)
        if trail:
            desc = f"{desc} {trail}".strip()

        if qty is None:
            try:
                qty = float(all_values.get("qty"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                qty = float(all_values.get("area") or 1)

        return QTORow(
            drawings=sheet,
            details=details or all_values.get("detail_ref", ""),
            description=desc,
            qty=qty or 0.0,
            units=self.units,
            trade_division=self.csi_division,
            extraction_method="assembly",
            confidence=0.99,    # human-confirmed inputs — high confidence
            needs_review=False,
        )


class AssemblyEngine:
    """Loads, validates, and applies assemblies from the ``assemblies/`` folder."""

    def __init__(self, directory: Path | str = _ASSEMBLIES_DIR):
        self._directory = Path(directory)
        self._assemblies: dict[str, Assembly] = {}
        self._load_errors: list[str] = []
        self.reload()

    # ── Loading / persistence ──────────────────────────────────────────────

    def reload(self) -> None:
        self._assemblies.clear()
        self._load_errors.clear()
        if not self._directory.exists():
            _LOG.info("assemblies directory missing at %s", self._directory)
            return
        for path in sorted(self._directory.glob("*.yaml")):
            try:
                self._load_file(path)
            except Exception as exc:
                self._load_errors.append(f"{path.name}: {exc}")
                _LOG.warning("failed to load assembly %s: %s", path, exc)

    def _load_file(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("top-level YAML must be a mapping")
        for required in ("key", "name", "description_template", "units"):
            if required not in data:
                raise ValueError(f"missing required field {required!r}")
        inputs = [AssemblyInput.from_dict(d) for d in (data.get("inputs") or [])]
        asm = Assembly(
            key=str(data["key"]),
            name=str(data["name"]),
            trade=str(data.get("trade", "general")),
            csi_division=str(data.get("csi_division", "")),
            units=str(data["units"]),
            description_template=str(data["description_template"]),
            inputs=inputs,
            math_trail_template=str(data.get("math_trail_template", "")),
            notes=str(data.get("notes", "")),
            source_file=path,
        )
        if asm.key in self._assemblies:
            raise ValueError(f"duplicate key {asm.key!r}")
        self._assemblies[asm.key] = asm

    def save_assembly(
        self,
        *,
        key: str,
        name: str,
        trade: str,
        csi_division: str,
        units: str,
        description_template: str,
        inputs: Iterable[AssemblyInput] = (),
        math_trail_template: str = "",
        notes: str = "",
    ) -> Path:
        """Write a new ``.yaml`` to ``assemblies/`` and reload."""
        if key in self._assemblies:
            raise ValueError(f"key {key!r} already exists")
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{key}.yaml"
        payload = {
            "key": key,
            "name": name,
            "trade": trade,
            "csi_division": csi_division,
            "units": units,
            "description_template": description_template,
            "inputs": [
                {
                    "name": inp.name,
                    "label": inp.label,
                    "type": inp.type,
                    "default": inp.default,
                    **({"options": inp.options} if inp.options else {}),
                    **({"units": inp.units} if inp.units else {}),
                }
                for inp in inputs
            ],
        }
        if math_trail_template:
            payload["math_trail_template"] = math_trail_template
        if notes:
            payload["notes"] = notes
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, sort_keys=False, width=120, indent=2)
        self.reload()
        return path

    # ── Lookup / apply ─────────────────────────────────────────────────────

    @property
    def errors(self) -> list[str]:
        return list(self._load_errors)

    def all(self) -> list[Assembly]:
        return list(self._assemblies.values())

    def get(self, key: str) -> Assembly:
        try:
            return self._assemblies[key]
        except KeyError as e:
            raise KeyError(f"unknown assembly: {key}") from e

    def by_trade(self) -> dict[str, list[Assembly]]:
        out: dict[str, list[Assembly]] = {}
        for asm in self._assemblies.values():
            out.setdefault(asm.trade or "general", []).append(asm)
        for v in out.values():
            v.sort(key=lambda a: a.name.lower())
        return dict(sorted(out.items()))

    def apply(
        self,
        key: str,
        values: dict[str, Any],
        *,
        sheet: str = "",
        details: str = "",
        qty: Optional[float] = None,
    ) -> QTORow:
        return self.get(key).apply(values, sheet=sheet, details=details, qty=qty)


def _validate_cli() -> int:
    """Run via ``python -m core.assembly_engine`` to lint every YAML."""
    eng = AssemblyEngine()
    print(f"Loaded {len(eng.all())} assemblies from {_ASSEMBLIES_DIR}")
    if eng.errors:
        print("ERRORS:")
        for e in eng.errors:
            print(f"  - {e}")
        return 1
    for a in eng.all():
        missing = a.required_input_names() - {i.name for i in a.inputs}
        if missing:
            print(f"  ! {a.key}: template references undeclared inputs: {sorted(missing)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_validate_cli())
