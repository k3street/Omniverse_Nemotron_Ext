"""
LayoutSpec schema migrations — forward-only.

Per spec §18: migrations are forward-only functions that take the raw spec
dict (post JSON parse, pre Pydantic validate) and return a dict transformed
to a newer version. Read path applies all migrations from the spec's
`version` to current. Failures preserve the original file with
`.broken-{timestamp}` suffix.

Discipline:
- Append-only — never delete a migration. Old data must always migrate
  forward through the chain.
- Forward-only — no rollback. If a migration fails irrecoverably, the
  fix is a new migration, not editing an existing one.
- Test every migration with a fixture (pre/post snapshots) before merge.

Migration registration (when migrations exist):

    from .v1_0_to_v1_1 import migrate as v1_0_to_v1_1
    MIGRATIONS = {
        "1.0": ("1.1", v1_0_to_v1_1),
        # "1.1": ("1.2", v1_1_to_v1_2),
    }
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)

# Empty until the first migration is needed (v1.0 is current).
# Format: {from_version: (to_version, migrate_fn)}
MIGRATIONS: Dict[str, Tuple[str, Callable[[dict], dict]]] = {}

CURRENT_VERSION = "1.0"


class MigrationError(Exception):
    """Raised when a migration step fails irrecoverably."""


def needs_migration(spec_dict: dict) -> bool:
    """True iff the spec is at an older version than CURRENT_VERSION."""
    version = spec_dict.get("version", CURRENT_VERSION)
    return version != CURRENT_VERSION


def migrate(spec_dict: dict) -> dict:
    """Apply all migrations from the spec's current version to CURRENT_VERSION.

    Returns the migrated dict. Raises MigrationError if no migration path
    exists from the current version (e.g., trying to load a file from a
    newer version than this code supports).
    """
    version = spec_dict.get("version", CURRENT_VERSION)
    if version == CURRENT_VERSION:
        return spec_dict

    while version != CURRENT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise MigrationError(
                f"no migration from version {version!r} to current "
                f"({CURRENT_VERSION!r}); spec was authored with a newer "
                "or unsupported schema"
            )
        target_version, migrate_fn = step
        logger.info(f"migrating spec: {version} → {target_version}")
        spec_dict = migrate_fn(spec_dict)
        spec_dict["version"] = target_version
        version = target_version

    return spec_dict


def quarantine_broken_file(path: Path) -> Path:
    """Move a file that failed migration to a timestamped `.broken-` sibling.

    Returns the new path. Caller can present the broken-file path to the
    user for manual recovery.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine = path.with_suffix(f"{path.suffix}.broken-{ts}")
    shutil.copy(path, quarantine)
    return quarantine


def safe_load_and_migrate(path: Path) -> dict:
    """Load a JSON spec file; migrate forward; on irrecoverable failure,
    quarantine the original and re-raise.

    This is the recommended entry point for read-paths that need
    migration discipline.
    """
    try:
        with open(path, encoding="utf-8") as f:
            spec_dict = json.load(f)
    except json.JSONDecodeError as e:
        broken_path = quarantine_broken_file(path)
        raise MigrationError(
            f"file {path} is not valid JSON; quarantined to {broken_path}"
        ) from e

    try:
        return migrate(spec_dict)
    except MigrationError:
        broken_path = quarantine_broken_file(path)
        logger.error(
            f"migration failed for {path}; quarantined to {broken_path}"
        )
        raise
