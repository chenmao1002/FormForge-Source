"""Human-readable assembly localization, separate from stable CAD identifiers.

Dimensions, part IDs, filenames and the physical design never depend on locale.
Exact project translations take priority over the renderer's fixed vocabulary.
"""
import json
from pathlib import Path
import re


def _assembly_warning_translations(design):
    """Predict only current kernel warning templates, without building geometry.

    Prefixes remain stable technical identifiers. This avoids duplicating digits
    from a translated display name and keeps the signed numeric multiset intact.
    Author text never qualifies for this exemption from exact translation.
    """
    from .drawing_translations import (KERNEL_WARNINGS, RECONSTRUCTION_WARNING,
        RECONSTRUCTION_WARNING_ZH, COUNTERSINK_WARNING, COUNTERSINK_WARNING_ZH,
        TAPPING_WARNING, TAPPING_WARNING_ZH, RADIAL_TAPPING_WARNING,
        RADIAL_TAPPING_WARNING_ZH)
    result = {}
    for part in design.parts:
        if not part.step:
            for hole in part.holes:
                prefix = part.name + "/" + hole.name + ": "
                if hole.countersink_diameter is not None and hole.countersink_depth() > hole.z1 - hole.z0:
                    result[prefix + COUNTERSINK_WARNING] = prefix + COUNTERSINK_WARNING_ZH
                if hole.kind == "tap":
                    result[prefix + hole.thread + TAPPING_WARNING] = prefix + hole.thread + TAPPING_WARNING_ZH
            for hole in part.radial_holes:
                if hole.kind == "tap":
                    prefix = part.name + "/" + hole.name + ": " + hole.thread
                    result[prefix + RADIAL_TAPPING_WARNING] = prefix + RADIAL_TAPPING_WARNING_ZH
        if part.model_provenance == "drawing_reconstruction":
            prefix = part.name + ": "
            result[prefix + RECONSTRUCTION_WARNING] = prefix + RECONSTRUCTION_WARNING_ZH
    for source, target in KERNEL_WARNINGS.items():
        if source.startswith("Motion checks") and not design.moving_groups:
            continue
        if source.startswith("Assembly insertion checks") and not design.insertion_checks:
            continue
        if source.startswith("Reducer rated input speed") and design.reducer_rated_input_rpm is not None:
            continue
        result[source] = target
    return result


def assembly_localization_warnings(design):
    """Warnings known before CAD, for validate/generate localization preflight."""
    return list(_assembly_warning_translations(design))


def language_code(value):
    normalized = value.replace("_", "-").casefold()
    if normalized == "en":
        return "en"
    if normalized in ("zh", "zh-cn"):
        return "zh-CN"
    raise ValueError("Supported languages: en, zh-CN")


class Localization:
    def __init__(self, value):
        if value.get("schema_version") != 1 or language_code(value.get("language", "")) != "zh-CN":
            raise ValueError("Expected a schema_version 1 zh-CN localization catalog")
        self.value = value
        self.part_names = self._strings(value.get("part_names"), "part_names")
        self.texts = self._strings(value.get("texts"), "texts")
        from .drawing_translations import PHRASES, KERNEL_WARNINGS
        self.kernel_texts = dict(KERNEL_WARNINGS)
        fragments = dict(PHRASES)
        fragments.update(self.texts)
        for source, target in self.texts.items():
            if source.casefold() == source and source.isascii():
                fragments.setdefault(source.upper(), target)
        fragments.update({name: translated + " [" + name + "]"
                          for name, translated in self.part_names.items()})
        # One substitution pass prevents a preserved identifier inside a
        # translated display name from being localized for a second time.
        self.fragments = fragments
        self.pattern = re.compile("|".join(re.escape(key) for key in sorted(fragments, key=len, reverse=True)))

    @staticmethod
    def _strings(value, field):
        if not isinstance(value, dict) or any(not isinstance(k, str) or not k or
                not isinstance(v, str) or not v.strip() for k, v in value.items()):
            raise ValueError(field + " must be a mapping of nonempty strings")
        return value

    @classmethod
    def read(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def text(self, value, language="en"):
        value = str(value)
        if language_code(language) == "en":
            return value
        if value in self.texts:
            return self.texts[value]
        if value in self.kernel_texts:
            return self.kernel_texts[value]
        def replace_at(match, offset):
            key = match.group()
            if key in self.part_names:
                start, end = offset + match.start(), offset + match.end()
                before = value[start - 1] if start else ""
                after = value[end] if end < len(value) else ""
                if any(c and (c.isascii() and c.isalnum() or c in "_./\\-") for c in (before, after)):
                    return key
            return self.fragments[key]
        # Paths, URLs and provenance digests are machine-readable literals.
        protected = re.compile(r"https?://[^\s|]+|[a-fA-F0-9]{64}|[^\s|]+\.(?:step|stp|pdf|glb|html|json|zip|csv|svg|dxf)\b")
        result, cursor = [], 0
        for literal in protected.finditer(value):
            # Use a slice-specific replacement so part boundary positions are
            # evaluated in the original full row, not against stale offsets.
            segment = value[cursor:literal.start()]
            result.append(self.pattern.sub(lambda m: replace_at(m, cursor), segment))
            result.append(literal.group())
            cursor = literal.end()
        result.append(self.pattern.sub(lambda m: replace_at(m, cursor), value[cursor:]))
        return "".join(result)


    def part(self, identifier, language="en"):
        if language_code(language) == "en":
            return identifier
        if identifier not in self.part_names:
            raise ValueError("Missing Chinese part name: " + identifier)
        return self.part_names[identifier]

    def role(self, value, language="en"):
        if language_code(language) == "en":
            return value
        return {"machined": "自制加工件", "purchased": "采购件", "fastener": "紧固件"}.get(value, value)

    def validate_design(self, design, warnings=None):
        """Require faithful exact translations of every author-supplied field."""
        kernel_texts = _assembly_warning_translations(design)
        warning_sources = set(kernel_texts).union(warnings or ())
        required = set(design.notes + design.assumptions)
        for part in design.parts:
            self.part(part.name, "zh-CN")
            required.update(part.notes)
            required.add(part.material)
            for feature in part.engineering_features:
                required.update(feature.notes)
                required.update(x for x in (feature.label, feature.gdt_type, feature.surface) if x)
            for entry in part.holes + part.keyways + part.radial_holes + part.profiles + part.edge_treatments:
                required.add(entry.name)
                if getattr(entry, "thread", None):
                    required.add(entry.thread)
        # The finite template set applies only to warnings. Custom notes,
        # materials and labels still need exact translations even if their text
        # happens to equal a kernel sentence.
        missing = sorted((required - self.texts.keys()) |
                         (warning_sources - self.texts.keys() - kernel_texts.keys()))
        if missing:
            raise ValueError("Missing exact Chinese translations: " + json.dumps(missing, ensure_ascii=False))
        # Decimal values/signs and numbers embedded in identifiers must survive
        # translation. This detects an accidentally changed machining limit.
        numeric = re.compile(r"[+-]?\d+(?:\.\d+)?")
        translations = {source: self.texts[source] for source in required}
        translations.update({source: self.texts.get(source, kernel_texts.get(source))
                             for source in warning_sources})
        mismatches = [source for source, target in translations.items()
                      if sorted(numeric.findall(source)) != sorted(numeric.findall(target))]
        if mismatches:
            raise ValueError("Translation changed numeric values: " + json.dumps(mismatches, ensure_ascii=False))
        self.kernel_texts.update(kernel_texts)
        return {"part_names": len(design.parts),
                "exact_texts": len(required | (warning_sources & self.texts.keys())),
                "kernel_template_warnings": len(warning_sources - self.texts.keys()),
                "missing": [], "numeric_mismatches": [],
                "numeric_check": "signed numeric token multiset; semantic mapping still requires review"}
