"""
src/config/loader.py — loads and validates the YAML source registry.

Reads every *.yaml file under sources/ (recursively), validates each entry
against src/models/source.py, and returns a single SourceRegistry.

Per 00_MASTER_PLAN.md section 8: "All configuration must be validated ...
Invalid source configuration must fail with a useful diagnostic message."
This module raises SourceConfigError with the offending file path and the
underlying pydantic validation error rather than crashing with a bare
traceback or silently skipping bad entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError

from src.models.source import SearchQueryConfig, SourceConfig, SourceRegistry

DEFAULT_SOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "sources"


class SourceConfigError(Exception):
    """Raised when a source YAML file fails to parse or validate.
    Carries the file path so the operator knows exactly what to fix.
    """

    def __init__(self, file_path: Path, message: str):
        self.file_path = file_path
        self.message = message
        super().__init__(f"{file_path}: {message}")


def _load_yaml_file(path: Path) -> list[dict]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SourceConfigError(path, f"invalid YAML syntax: {e}") from e
    if raw is None:
        return []
    if isinstance(raw, dict):
        # Allow a single-entry file to be written as a bare mapping.
        raw = [raw]
    if not isinstance(raw, list):
        raise SourceConfigError(
            path, f"expected a YAML list of entries (or a single mapping), got {type(raw).__name__}"
        )
    return raw


def load_source_registry(
    sources_dir: Optional[Path] = None,
    *,
    strict: bool = True,
) -> SourceRegistry:
    """Load and validate every source YAML file under `sources_dir`.

    Directory convention:
      sources/rss/*.yaml      -> entries with source_type: rss
      sources/vendors/*.yaml  -> entries with source_type: vendor or html
      sources/search/*.yaml   -> entries are SearchQueryConfig, not SourceConfig

    `strict=True` (default) raises SourceConfigError on the first invalid
    entry. `strict=False` collects and returns errors instead of raising,
    for tooling that wants to report *all* problems in one pass (e.g. a
    `validate-sources` CLI or a health-check script).
    """
    sources_dir = sources_dir or DEFAULT_SOURCES_DIR
    if not sources_dir.exists():
        raise SourceConfigError(sources_dir, "sources directory does not exist")

    sources: list[SourceConfig] = []
    queries: list[SearchQueryConfig] = []
    errors: list[SourceConfigError] = []

    search_dir = sources_dir / "search"
    yaml_files = sorted(sources_dir.rglob("*.yaml")) + sorted(sources_dir.rglob("*.yml"))

    for path in yaml_files:
        is_search_file = search_dir in path.parents or path.parent == search_dir
        try:
            entries = _load_yaml_file(path)
        except SourceConfigError as e:
            if strict:
                raise
            errors.append(e)
            continue

        for i, entry in enumerate(entries):
            try:
                if is_search_file:
                    queries.append(SearchQueryConfig(**entry))
                else:
                    sources.append(SourceConfig(**entry))
            except ValidationError as e:
                err = SourceConfigError(
                    path, f"entry #{i} ({entry.get('id', '?')!r}) failed validation:\n{e}"
                )
                if strict:
                    raise err from e
                errors.append(err)
            except TypeError as e:
                err = SourceConfigError(path, f"entry #{i}: {e}")
                if strict:
                    raise err from e
                errors.append(err)

    if not strict and errors:
        # Non-strict callers get a registry built from everything that DID
        # validate, plus access to the errors via the raised summary if they
        # want to fail loudly after inspecting individual problems.
        registry = SourceRegistry(sources=sources, search_queries=queries)
        registry._load_errors = errors  # type: ignore[attr-defined]
        return registry

    return SourceRegistry(sources=sources, search_queries=queries)


def validate_all(sources_dir: Optional[Path] = None) -> tuple[SourceRegistry, list[SourceConfigError]]:
    """Non-strict validation entry point for CLI/test use: returns whatever
    validated successfully plus a list of every error encountered, instead
    of stopping at the first bad file.
    """
    sources_dir = sources_dir or DEFAULT_SOURCES_DIR
    if not sources_dir.exists():
        raise SourceConfigError(sources_dir, "sources directory does not exist")

    sources: list[SourceConfig] = []
    queries: list[SearchQueryConfig] = []
    errors: list[SourceConfigError] = []
    seen_ids: set[str] = set()

    search_dir = sources_dir / "search"
    yaml_files = sorted(sources_dir.rglob("*.yaml")) + sorted(sources_dir.rglob("*.yml"))

    for path in yaml_files:
        is_search_file = search_dir in path.parents or path.parent == search_dir
        try:
            entries = _load_yaml_file(path)
        except SourceConfigError as e:
            errors.append(e)
            continue

        for i, entry in enumerate(entries):
            entry_id = entry.get("id", f"<no id, entry #{i}>") if isinstance(entry, dict) else f"<entry #{i}>"
            try:
                if is_search_file:
                    q = SearchQueryConfig(**entry)
                    if q.id in seen_ids:
                        errors.append(SourceConfigError(path, f"duplicate id across registry: {q.id!r}"))
                        continue
                    seen_ids.add(q.id)
                    queries.append(q)
                else:
                    s = SourceConfig(**entry)
                    if s.id in seen_ids:
                        errors.append(SourceConfigError(path, f"duplicate id across registry: {s.id!r}"))
                        continue
                    seen_ids.add(s.id)
                    sources.append(s)
            except ValidationError as e:
                errors.append(SourceConfigError(path, f"entry {entry_id!r} failed validation:\n{e}"))
            except TypeError as e:
                errors.append(SourceConfigError(path, f"entry {entry_id!r}: {e}"))

    registry = SourceRegistry(sources=sources, search_queries=queries)
    return registry, errors
