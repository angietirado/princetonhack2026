"""Bounded, versioned operator catalogs of frozen wallet snapshots.

Loading never calls Nessie and never substitutes fixtures. Projection caches are not
stored in this format: each context is recomputed from its validated frozen base.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from spending_api.lab_wallet import WalletDataError, project_wallet, validate_wallet

SCHEMA_VERSION = 1
MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_PROFILES = 20
MAX_FINANCIAL_RECORDS = 20_000
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PROFILE_FIELDS = frozenset({"profile_id", "profile_name", "description", "snapshot", "projection_inputs"})
WRITE_MODES = frozenset({"create", "append", "replace"})


class WalletCatalogError(ValueError):
    """A fixed error code, never an embedded source record, identifier, or path."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Wallet catalog rejected: {code}")


def _encoded(payload) -> bytes:
    try:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WalletCatalogError("invalid_catalog") from None
    if len(data) > MAX_CATALOG_BYTES:
        raise WalletCatalogError("catalog_size_limit")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WalletCatalogError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise WalletCatalogError("invalid_json")


def _profile_metadata(record):
    if not isinstance(record, dict) or set(record) != PROFILE_FIELDS:
        raise WalletCatalogError("invalid_profile")
    identity = record["profile_id"]
    if not isinstance(identity, str) or not PROFILE_ID.fullmatch(identity):
        raise WalletCatalogError("invalid_profile_id")
    for name, maximum, empty_allowed in (("profile_name", 120, False), ("description", 600, True)):
        value = record[name]
        if (
            not isinstance(value, str) or len(value) > maximum
            or value != value.strip() or (not empty_allowed and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise WalletCatalogError("invalid_profile_label")


def _context(record):
    _profile_metadata(record)
    try:
        # Lazy import keeps this foundation independent of financial-catalog startup.
        # The existing financial module remains the authority for projection inputs.
        from spending_api.lab_financial import ProjectionInputs

        snapshot = validate_wallet(record["snapshot"])
        inputs = ProjectionInputs.model_validate(record["projection_inputs"]).model_dump(mode="json")
        projection = project_wallet(snapshot, **inputs)
    except (WalletDataError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise WalletCatalogError("invalid_financial_context") from None
    return {
        "profile_id": record["profile_id"],
        "profile_name": record["profile_name"],
        "description": record["description"],
        "snapshot": snapshot,
        "projection_inputs": inputs,
        "projection": projection,
    }


def validate_wallet_catalog(payload: object) -> dict[str, dict]:
    """Validate one envelope and return fresh financial_context-compatible objects."""
    _encoded(payload)
    if (
        not isinstance(payload, dict) or set(payload) != {"schema_version", "profiles"}
        or type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION
        or not isinstance(payload["profiles"], list) or not 1 <= len(payload["profiles"]) <= MAX_PROFILES
    ):
        raise WalletCatalogError("invalid_envelope")
    catalog, record_count = {}, 0
    for record in payload["profiles"]:
        _profile_metadata(record)
        identity = record["profile_id"]
        if identity in catalog:
            raise WalletCatalogError("duplicate_profile_id")
        snapshot = record["snapshot"]
        if not isinstance(snapshot, dict):
            raise WalletCatalogError("invalid_financial_context")
        for collection in ("accounts", "obligations", "transactions"):
            if not isinstance(snapshot.get(collection), list):
                raise WalletCatalogError("invalid_financial_context")
            record_count += len(snapshot[collection])
        if record_count > MAX_FINANCIAL_RECORDS:
            raise WalletCatalogError("financial_record_limit")
        catalog[identity] = _context(record)
    return catalog


def load_wallet_catalog(path: str | Path) -> dict[str, dict]:
    """Read one explicitly configured regular file; every failure is fail-closed."""
    try:
        target = Path(path)
        if not stat.S_ISREG(target.stat().st_mode):
            raise WalletCatalogError("catalog_not_regular")
        with target.open("rb") as stream:
            data = stream.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise WalletCatalogError("catalog_size_limit")
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    except WalletCatalogError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise WalletCatalogError("catalog_read_error") from None
    return validate_wallet_catalog(payload)


def make_wallet_profile(
    *, profile_id: str, profile_name: str, snapshot: dict,
    description: str = "", projection_inputs: dict | None = None,
) -> dict:
    """Return one normalized on-disk profile without changing its source label."""
    record = {
        "profile_id": profile_id, "profile_name": profile_name, "description": description,
        "snapshot": snapshot, "projection_inputs": {} if projection_inputs is None else projection_inputs,
    }
    context = validate_wallet_catalog({"schema_version": SCHEMA_VERSION, "profiles": [record]})[profile_id]
    return {key: copy.deepcopy(context[key]) for key in PROFILE_FIELDS}


def _target_catalog(path: Path, profile_id: str, mode: str) -> dict[str, dict]:
    if mode not in WRITE_MODES:
        raise WalletCatalogError("invalid_write_mode")
    if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id):
        raise WalletCatalogError("invalid_profile_id")
    if path.is_symlink():
        raise WalletCatalogError("symlink_output")
    exists = path.exists()
    if mode == "create":
        if exists:
            raise WalletCatalogError("catalog_already_exists")
        return {}
    if not exists:
        raise WalletCatalogError("catalog_missing")
    catalog = load_wallet_catalog(path)
    if mode == "append" and profile_id in catalog:
        raise WalletCatalogError("profile_already_exists")
    if mode == "replace" and profile_id not in catalog:
        raise WalletCatalogError("profile_missing")
    return catalog


def check_wallet_profile_target(path: str | Path, profile_id: str, *, mode: str = "create") -> None:
    """Preflight explicit create/append/replace semantics before a network capture."""
    try:
        target = Path(path)
        if not target.parent.is_dir():
            raise WalletCatalogError("output_directory_missing")
        _target_catalog(target, profile_id, mode)
        if target.with_name(target.name + ".lock").exists():
            raise WalletCatalogError("catalog_locked")
    except WalletCatalogError:
        raise
    except (OSError, TypeError, ValueError):
        raise WalletCatalogError("catalog_io_error") from None


def write_wallet_profile(path: str | Path, record: dict, *, mode: str = "create") -> dict:
    """Atomically create, append a new ID, or replace an existing ID under a lock.

    The exclusive sibling .lock is cooperative. Never remove another writer's lock.
    A crash can leave a lock; an operator must establish that no writer is active
    before removing it. Readers see the complete old or complete new catalog.
    """
    context = validate_wallet_catalog({"schema_version": SCHEMA_VERSION, "profiles": [record]})[record["profile_id"]]
    target, lock_path, temporary = None, None, None
    lock_fd = None
    try:
        target = Path(path)
        if not target.parent.is_dir():
            raise WalletCatalogError("output_directory_missing")
        lock_path = target.with_name(target.name + ".lock")
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise WalletCatalogError("catalog_locked") from None
        catalog = _target_catalog(target, context["profile_id"], mode)
        catalog[context["profile_id"]] = context
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "profiles": [{key: item[key] for key in PROFILE_FIELDS} for _, item in sorted(catalog.items())],
        }
        validate_wallet_catalog(envelope)
        data = _encoded(envelope) + b"\n"
        if len(data) > MAX_CATALOG_BYTES:
            raise WalletCatalogError("catalog_size_limit")
        descriptor, temporary = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".tmp", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode == "create":
            # Publish the complete temporary file without replacing a destination
            # created since preflight, even by a writer that ignores our lock.
            # A filesystem without hard-link support fails closed; do not fall
            # back to a racy existence check plus replace. Finally removes our
            # temporary link after successful publication or any failure.
            try:
                os.link(temporary, target)
            except FileExistsError:
                raise WalletCatalogError("catalog_already_exists") from None
        else:
            os.replace(temporary, target)
            temporary = None
        return context
    except WalletCatalogError:
        raise
    except (OSError, TypeError, ValueError):
        raise WalletCatalogError("catalog_write_error") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass
