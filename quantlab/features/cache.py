from __future__ import annotations

"""Opt-in immutable SQLite-entry cache for Phase 2.2 feature results.

The standard-library codec is deliberate: pyarrow is installed locally but is
not a declared project dependency.  Each entry is an independently readable
SQLite file plus a canonical JSON manifest; no pickle or object serialization
is used.  A feature implementation change must bump its explicit feature
version, because the physical cache format is not part of feature identity.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

import numpy as np
import pandas as pd

from .contracts import FeatureComputationIdentity, FeatureResult, canonical_json


_FORMAT_VERSION = "v1"
_CODEC = "sqlite-json-v1"
_NPZ_CODEC = "npz_numeric_v1"


class CacheCorruptionError(RuntimeError):
    """A published entry exists but cannot be trusted or decoded."""


@dataclass(frozen=True, slots=True)
class CacheWriteResult:
    hit: bool
    cache_key: str
    codec: str
    relative_path: str
    row_count: int
    content_checksum: str


def _identity_payload(identity: FeatureComputationIdentity) -> dict[str, Any]:
    feature = identity.feature_identity
    return {
        "hash": identity.sha256,
        "feature_identity_hash": feature.sha256,
        "feature_request": feature.request.canonical(),
        "dependency_hashes": list(feature.dependency_hashes),
        "definition_metadata": {key: value for key, value in feature.definition_metadata},
        "snapshot_id": identity.snapshot_id,
        "symbols": list(identity.requested_symbols),
        "start_date": identity.start_date,
        "through_date": identity.through_date,
        "universe_identity": identity.universe_identity,
    }


def _assert_key(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("feature computation identity must be a lowercase SHA-256 hex digest")
    return value


def _encode_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return {"timestamp": value.isoformat()}
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported cached scalar type: {type(value).__name__}")


def _supported_dtype(dtype: Any) -> bool:
    return pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_datetime64_any_dtype(dtype)


def _supported_column(column: str, series: pd.Series) -> bool:
    if _supported_dtype(series.dtype):
        return True
    # This is the sole authoritative categorical field introduced by the
    # MARKET feature.  NPZ writes Unicode plus an explicit null mask.
    return column in {"Market_Regime", "paper_v2_state"} and all(pd.isna(value) or isinstance(value, str) for value in series)


def _frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    unsupported = [column for column in frame.columns if not _supported_column(column, frame[column])]
    if unsupported:
        raise TypeError("unsupported object-valued feature columns: " + ", ".join(unsupported))
    if isinstance(frame.index, pd.RangeIndex):
        index = {"kind": "range", "start": frame.index.start, "stop": frame.index.stop, "step": frame.index.step, "name": frame.index.name}
    else:
        if not _supported_dtype(frame.index.dtype):
            raise TypeError("unsupported feature index dtype")
        index = {"kind": "values", "dtype": str(frame.index.dtype), "name": frame.index.name, "values": [_encode_value(value) for value in frame.index]}
    return {"columns": list(frame.columns), "dtypes": [str(frame[column].dtype) for column in frame.columns], "index": index, "rows": [[_encode_value(value) for value in row] for row in frame.itertuples(index=False, name=None)]}


def _decode_value(value: Any, dtype: str) -> Any:
    if isinstance(value, dict) and set(value) == {"timestamp"}:
        return pd.Timestamp(value["timestamp"])
    return value


def _frame_from_payload(payload: Mapping[str, Any]) -> pd.DataFrame:
    columns, dtypes = payload["columns"], payload["dtypes"]
    if not isinstance(columns, list) or not isinstance(dtypes, list) or len(columns) != len(dtypes):
        raise CacheCorruptionError("invalid frame schema")
    decoded = [[_decode_value(value, dtype) for value, dtype in zip(row, dtypes, strict=True)] for row in payload["rows"]]
    frame = pd.DataFrame(decoded, columns=columns)
    try:
        for column, dtype in zip(columns, dtypes, strict=True):
            frame[column] = pd.to_datetime(frame[column]) if dtype.startswith("datetime64") else frame[column].astype(dtype)
    except (TypeError, ValueError) as error:
        raise CacheCorruptionError(f"cached dtype reconstruction failed: {error}") from error
    index = payload["index"]
    if index["kind"] == "range":
        frame.index = pd.RangeIndex(index["start"], index["stop"], index["step"], name=index.get("name"))
    elif index["kind"] == "values":
        values = [_decode_value(value, index["dtype"]) for value in index["values"]]
        frame.index = pd.Index(values, dtype=index["dtype"], name=index.get("name"))
    else:
        raise CacheCorruptionError("invalid cached index kind")
    return frame


class PreparedFeatureCache:
    """Explicit-root, immutable prepared feature cache."""

    def __init__(self, cache_root: str | Path, *, codec: str = _CODEC) -> None:
        if codec not in {_CODEC, _NPZ_CODEC}:
            raise ValueError(f"unknown prepared feature cache codec: {codec}")
        self._root = Path(cache_root).expanduser().resolve()
        self._codec = codec

    @property
    def cache_root(self) -> Path:
        return self._root

    def _entry_path(self, identity: FeatureComputationIdentity) -> Path:
        key = _assert_key(identity.sha256)
        prefix = () if self._codec == _CODEC else (self._codec,)
        return self._root.joinpath(*prefix, _FORMAT_VERSION, key[:2], key)

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self._root).as_posix()
        except ValueError as error:  # defensive: no entry may escape its root
            raise CacheCorruptionError("cache entry escaped explicit root") from error

    def _checksum(self, identity: FeatureComputationIdentity, available: tuple[str, ...], missing: tuple[str, ...], metadata: Mapping[str, Any], frames: Mapping[str, pd.DataFrame]) -> tuple[str, dict[str, Any]]:
        payload = {"identity": _identity_payload(identity), "available_symbols": list(available), "missing_symbols": list(missing), "metadata": dict(metadata), "frames": {symbol: _frame_payload(frame) for symbol, frame in sorted(frames.items())}}
        return sha256(canonical_json(payload)).hexdigest(), payload

    def _read_entry(self, path: Path, identity: FeatureComputationIdentity) -> FeatureResult:
        if self._codec == _NPZ_CODEC:
            return self._read_npz_entry(path, identity)
        manifest_path, data_path = path / "manifest.json", path / "data.sqlite"
        if not manifest_path.is_file() or not data_path.is_file():
            raise CacheCorruptionError(f"incomplete cache entry: {path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CacheCorruptionError(f"invalid cache manifest: {path}") from error
        if manifest.get("complete") is not True or manifest.get("format_version") != _FORMAT_VERSION or manifest.get("codec") != self._codec:
            raise CacheCorruptionError(f"invalid cache completion marker: {path}")
        if manifest.get("computation_identity") != _identity_payload(identity):
            raise CacheCorruptionError(f"cache identity mismatch: {path}")
        try:
            connection = sqlite3.connect(data_path.as_uri() + "?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT symbol, ordinal, payload FROM frames ORDER BY ordinal").fetchall()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise CacheCorruptionError(f"invalid cache data: {path}") from error
        frames = {symbol: _frame_from_payload(json.loads(payload)) for symbol, _ordinal, payload in rows}
        available, missing = tuple(manifest["available_symbols"]), tuple(manifest["missing_symbols"])
        checksum, _ = self._checksum(identity, available, missing, manifest["feature_metadata"], frames)
        if checksum != manifest.get("content_checksum"):
            raise CacheCorruptionError(f"cache content checksum mismatch: {path}")
        cache_metadata = {"hit": True, "cache_key": identity.sha256, "codec": self._codec, "relative_path": self._relative(path), "row_count": manifest["row_count"], "content_checksum": checksum}
        metadata = MappingProxyType({**manifest["feature_metadata"], "cache": MappingProxyType(cache_metadata)})
        return FeatureResult(identity, metadata, available, missing, MappingProxyType(frames))

    def _read_npz_entry(self, path: Path, identity: FeatureComputationIdentity) -> FeatureResult:
        manifest_path, data_path = path / "manifest.json", path / "data.npz"
        if not manifest_path.is_file() or not data_path.is_file():
            raise CacheCorruptionError(f"incomplete cache entry: {path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CacheCorruptionError("invalid cache manifest") from error
        if manifest.get("complete") is not True or manifest.get("codec") != _NPZ_CODEC or manifest.get("computation_identity") != _identity_payload(identity):
            raise CacheCorruptionError("invalid NPZ cache manifest")
        if sha256(data_path.read_bytes()).hexdigest() != manifest.get("physical_sha256"):
            raise CacheCorruptionError("NPZ physical checksum mismatch")
        expected = set(); frames = {}
        try:
            with np.load(data_path, allow_pickle=False) as archive:
                for item in manifest["frames"]:
                    keys = [
                        item["index_key"],
                        *(key for column in item["columns"] for key in (
                            (column["key"], column["valid_key"])
                            if column.get("kind") == "string" else (column["key"],)
                        )),
                    ]
                    expected.update(keys)
                    if any(key not in archive for key in keys):
                        raise CacheCorruptionError("missing NPZ member")
                    data = {}
                    for column in item["columns"]:
                        values = archive[column["key"]]
                        if column.get("kind") == "string":
                            restored = pd.Series(values.astype(str), dtype=column["dtype"])
                            restored.loc[~archive[column["valid_key"]].astype(bool, copy=False)] = np.nan
                            data[column["name"]] = restored
                        else:
                            data[column["name"]] = values.astype(np.dtype(column["dtype"]), copy=False)
                    frame = pd.DataFrame(data, columns=[column["name"] for column in item["columns"]])
                    index = archive[item["index_key"]]
                    if item["index"]["kind"] == "range":
                        frame.index = pd.RangeIndex(item["index"]["start"], item["index"]["stop"], item["index"]["step"], name=item["index"].get("name"))
                    else:
                        frame.index = pd.Index(index.astype(np.dtype(item["index"]["dtype"]), copy=False), name=item["index"].get("name"))
                    if len(frame) != item["row_count"]:
                        raise CacheCorruptionError("NPZ row count mismatch")
                    frames[item["symbol"]] = frame
                if set(archive.files) != expected:
                    raise CacheCorruptionError("unexpected NPZ member")
        except (OSError, ValueError) as error:
            raise CacheCorruptionError("invalid NPZ payload") from error
        available, missing = tuple(manifest["available_symbols"]), tuple(manifest["missing_symbols"])
        metadata = MappingProxyType({**manifest["feature_metadata"], "cache": MappingProxyType({"hit": True, "cache_key": identity.sha256, "codec": _NPZ_CODEC, "relative_path": self._relative(path), "row_count": manifest["row_count"], "content_checksum": manifest["content_checksum"]})})
        return FeatureResult(identity, metadata, available, missing, MappingProxyType(frames))

    def get(self, identity: FeatureComputationIdentity) -> FeatureResult | None:
        path = self._entry_path(identity)
        if not path.exists():
            return None
        return self._read_entry(path, identity)

    def with_write_metadata(self, result: FeatureResult, write: CacheWriteResult) -> FeatureResult:
        """Return the miss/winner result with immutable observability only."""
        metadata = MappingProxyType({
            **dict(result.metadata),
            "cache": MappingProxyType({
                "hit": write.hit,
                "cache_key": write.cache_key,
                "codec": write.codec,
                "relative_path": write.relative_path,
                "row_count": write.row_count,
                "content_checksum": write.content_checksum,
            }),
        })
        return FeatureResult(result.computation_identity, metadata, result.available_symbols, result.missing_symbols, result._frames)

    def put(self, result: FeatureResult) -> CacheWriteResult:
        if self._codec == _NPZ_CODEC:
            return self._put_npz(result)
        identity = result.computation_identity; final = self._entry_path(identity)
        if final.exists():
            existing = self._read_entry(final, identity)
            cache = existing.metadata["cache"]
            return CacheWriteResult(True, identity.sha256, _CODEC, str(cache["relative_path"]), int(cache["row_count"]), str(cache["content_checksum"]))
        # Validate serialization before this method creates any directory.
        checksum, _payload = self._checksum(identity, result.available_symbols, result.missing_symbols, result.metadata, result._frames)
        final.parent.mkdir(parents=True, exist_ok=True)
        # Keep the sibling short enough for conservative Windows path limits;
        # the validated key is already represented by its parent/final path.
        temporary = final.parent / f".{uuid4().hex}.tmp"
        if temporary.parent.resolve() != final.parent.resolve():
            raise CacheCorruptionError("unsafe temporary cache path")
        try:
            temporary.mkdir()
            data_path = temporary / "data.sqlite"
            connection = sqlite3.connect(data_path)
            try:
                connection.execute("CREATE TABLE frames (symbol TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, payload TEXT NOT NULL)")
                for ordinal, (symbol, frame) in enumerate(sorted(result._frames.items())):
                    connection.execute("INSERT INTO frames VALUES (?, ?, ?)", (symbol, ordinal, canonical_json(_frame_payload(frame)).decode("utf-8")))
                connection.commit()
            finally:
                connection.close()
            manifest = {"format_version": _FORMAT_VERSION, "codec": _CODEC, "computation_identity": _identity_payload(identity), "available_symbols": list(result.available_symbols), "missing_symbols": list(result.missing_symbols), "feature_metadata": dict(result.metadata), "output_schema": {symbol: {"columns": list(frame.columns), "dtypes": [str(frame[column].dtype) for column in frame.columns]} for symbol, frame in sorted(result._frames.items())}, "row_count": sum(len(frame) for frame in result._frames.values()), "content_checksum": checksum, "complete": True}
            (temporary / "manifest.json").write_bytes(canonical_json(manifest))
            self._read_entry(temporary, identity)
            try:
                os.rename(temporary, final)
                hit = False
            except OSError:
                # A same-key concurrent writer may have won.  Never replace it.
                if not final.exists():
                    raise
                hit = True
            if hit:
                existing = self._read_entry(final, identity)
                cache = existing.metadata["cache"]
                return CacheWriteResult(True, identity.sha256, _CODEC, str(cache["relative_path"]), int(cache["row_count"]), str(cache["content_checksum"]))
            return CacheWriteResult(False, identity.sha256, _CODEC, self._relative(final), manifest["row_count"], checksum)
        finally:
            # Only remove our uniquely named incomplete sibling; never a final entry.
            if temporary.exists():
                for child in temporary.iterdir():
                    child.unlink()
                temporary.rmdir()

    def _put_npz(self, result: FeatureResult) -> CacheWriteResult:
        identity = result.computation_identity; final = self._entry_path(identity)
        if final.exists():
            existing = self._read_entry(final, identity); cache = existing.metadata["cache"]
            return CacheWriteResult(True, identity.sha256, _NPZ_CODEC, str(cache["relative_path"]), int(cache["row_count"]), str(cache["content_checksum"]))
        checksum, _ = self._checksum(identity, result.available_symbols, result.missing_symbols, result.metadata, result._frames)
        final.parent.mkdir(parents=True, exist_ok=True); temporary = final.parent / f".{uuid4().hex}.tmp"; temporary.mkdir()
        try:
            arrays: dict[str, np.ndarray] = {}; frame_manifest = []
            for ordinal, (symbol, frame) in enumerate(sorted(result._frames.items())):
                unsupported = [column for column in frame.columns if not _supported_column(column, frame[column])]
                if unsupported: raise TypeError("unsupported object-valued feature columns: " + ", ".join(unsupported))
                prefix = f"frame_{ordinal:05d}"; index_key = prefix + "_index"
                if isinstance(frame.index, pd.RangeIndex):
                    arrays[index_key] = np.arange(frame.index.start, frame.index.stop, frame.index.step, dtype=np.int64); index_meta = {"kind": "range", "start": frame.index.start, "stop": frame.index.stop, "step": frame.index.step, "name": frame.index.name}
                else:
                    if not _supported_dtype(frame.index.dtype): raise TypeError("unsupported feature index dtype")
                    arrays[index_key] = frame.index.to_numpy(copy=True); index_meta = {"kind": "values", "dtype": str(frame.index.dtype), "name": frame.index.name}
                columns = []
                for column_ordinal, column in enumerate(frame.columns):
                    key = f"{prefix}_column_{column_ordinal:03d}"
                    if not _supported_dtype(frame[column].dtype):
                        values = frame[column]
                        width = max(1, max((len(value) for value in values.dropna().astype(str)), default=0))
                        arrays[key] = np.asarray(values.fillna("").astype(str), dtype=f"<U{width}")
                        valid_key = key + "_valid"; arrays[valid_key] = values.notna().to_numpy(dtype=bool)
                        columns.append({"name": column, "key": key, "valid_key": valid_key, "dtype": str(frame[column].dtype), "kind": "string"})
                    else:
                        arrays[key] = frame[column].to_numpy(copy=True)
                        columns.append({"name": column, "key": key, "dtype": str(frame[column].dtype)})
                frame_manifest.append({"symbol": symbol, "index_key": index_key, "index": index_meta, "columns": columns, "row_count": len(frame)})
            data_path = temporary / "data.npz"; np.savez(data_path, **arrays)
            physical = sha256(data_path.read_bytes()).hexdigest()
            manifest = {"format_version": _FORMAT_VERSION, "codec": _NPZ_CODEC, "computation_identity": _identity_payload(identity), "available_symbols": list(result.available_symbols), "missing_symbols": list(result.missing_symbols), "feature_metadata": dict(result.metadata), "frames": frame_manifest, "row_count": sum(len(frame) for frame in result._frames.values()), "content_checksum": checksum, "physical_sha256": physical, "complete": True}
            (temporary / "manifest.json").write_bytes(canonical_json(manifest))
            self._read_npz_entry(temporary, identity)
            try: os.rename(temporary, final); hit = False
            except OSError:
                if not final.exists(): raise
                hit = True
            if hit:
                existing = self._read_entry(final, identity); cache = existing.metadata["cache"]
                return CacheWriteResult(True, identity.sha256, _NPZ_CODEC, str(cache["relative_path"]), int(cache["row_count"]), str(cache["content_checksum"]))
            return CacheWriteResult(False, identity.sha256, _NPZ_CODEC, self._relative(final), manifest["row_count"], checksum)
        finally:
            if temporary.exists():
                for child in temporary.iterdir(): child.unlink()
                temporary.rmdir()

    def get_or_compute(self, identity: FeatureComputationIdentity, compute_fn: Callable[[], FeatureResult]) -> FeatureResult:
        cached = self.get(identity)
        if cached is not None:
            return cached
        result = compute_fn()
        if result.computation_identity != identity:
            raise ValueError("computed result identity does not match cache key")
        return self.with_write_metadata(result, self.put(result))
