"""Bounded NOAA archive discovery and content-addressed evidence receipts.

This module does not infer historical publication time from S3 Last-Modified,
download an entire archive, or turn catalogue evidence into a skill dataset.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode

from .ingest import NOAAAIWPCatalog

NOAA_ENDPOINT = "https://noaa-oar-mlwp-data.s3.amazonaws.com/"
DEFAULT_BYTE_BUDGET = 10_000_000
DEFAULT_PREFIXES = (
    "GRAP_v100_GFS/2024/0701/",
    "PANG_v100_GFS/2024/0701/",
    "FOUR_v200_GFS/2024/0701/",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("byte_budget must be a positive integer")
    return value


def _key(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 1024
            or value.startswith("/") or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Expected a relative NOAA object key without traversal or control characters")
    return value


def _client(session: Any) -> Any:
    if session is not None:
        return session
    import requests
    return requests


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_json(path: Path, value: Any) -> None:
    _write_atomic(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def _check_response(response: Any, limit: int, expected_size: int | None = None) -> int | None:
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Only complete HTTP 200 responses are accepted; redirects and partial responses are forbidden")
    if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
        raise ValueError("Compressed HTTP transfer encoding is not accepted for byte-exact evidence")
    raw_size = response.headers.get("Content-Length")
    size = int(raw_size) if raw_size is not None else None
    if size is not None and (size < 0 or size > limit):
        raise ValueError("Object Content-Length exceeds the byte budget or is invalid")
    if size is not None and expected_size is not None and size != expected_size:
        raise ValueError("Object Content-Length differs from the expected catalogue size")
    return size


def download_noaa_object(
    key: str,
    output_dir: str | Path,
    *,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Download one explicit NOAA object and write an atomic SHA-256 receipt.

    A known over-budget catalogue size is rejected before any HTTP request.
    Otherwise headers and streamed bytes independently enforce the byte budget.
    ETag is retained as metadata; multipart ETags are not content checksums.
    No arbitrary URLs, redirects, range reads, or credentials are accepted.
    """
    key, byte_budget = _key(key), _budget(byte_budget)
    if expected_size is not None:
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            raise ValueError("expected_size must be a nonnegative integer")
        if expected_size > byte_budget:
            raise ValueError("Expected catalogue size exceeds the byte budget")
    if expected_sha256 is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("expected_sha256 must contain 64 hexadecimal characters")
    output_dir = Path(output_dir)
    url = NOAA_ENDPOINT + quote(key, safe="/")
    response = _client(session).get(url, timeout=(5, 30), allow_redirects=False,
                                    stream=True, headers={"Accept-Encoding": "identity"})
    temporary = None
    try:
        declared_size = _check_response(response, byte_budget, expected_size)
        output_dir.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".download-", dir=output_dir)
        checksum, count = hashlib.sha256(), 0
        with os.fdopen(handle, "wb") as stream:
            for chunk in response.iter_content(chunk_size=65536):
                count += len(chunk)
                if count > byte_budget:
                    raise ValueError("Stream exceeds the byte budget")
                checksum.update(chunk)
                stream.write(chunk)
        if any(size is not None and size != count for size in (declared_size, expected_size)):
            raise ValueError("Downloaded byte count differs from declared or expected size")
        digest = checksum.hexdigest()
        if expected_sha256 and digest != expected_sha256.lower():
            raise ValueError("Downloaded SHA-256 differs from expected checksum")
        # Object keys never become directory names or arbitrary filesystem paths.
        suffix = Path(key).suffix.lower()
        suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"
        target = output_dir / f"{digest}{suffix}"
        os.replace(temporary, target)
        temporary = None
        receipt = {
            "schema_version": 1, "retrieved_at": _now(), "source_url": url,
            "object_key": key, "bytes": count, "sha256": digest,
            "local_file": target.name, "http_status": response.status_code,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_type": response.headers.get("Content-Type"),
            "data_kind": "real", "provider": "NOAA/CIRA AIWP archive",
            "evidence_kind": "provider_documentation" if key == "README.txt" else "downloaded_object",
            "byte_budget": byte_budget, "meteorological_content_validated": False,
            "historical_publication_time_verified": False, "skill_verified": False,
            "integrity_note": "SHA-256 identifies the downloaded bytes; ETag is provider metadata, not assumed to be a checksum.",
        }
        _write_json(output_dir / f"{digest}.receipt.json", receipt)
        return receipt
    finally:
        response.close()
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class _RecordedResponse:
    def __init__(self, response: Any, remaining_budget: int):
        self.response = response
        self.body = bytearray()
        self.remaining_budget = remaining_budget
        self.status_code = response.status_code
        self.headers = response.headers

    def raise_for_status(self):
        _check_response(self.response, self.remaining_budget)

    def iter_content(self, chunk_size: int):
        for chunk in self.response.iter_content(chunk_size=chunk_size):
            if len(self.body) + len(chunk) > self.remaining_budget:
                raise ValueError("Catalogue stream exceeds the total probe byte budget")
            self.body.extend(chunk)
            yield chunk
        length = self.headers.get("Content-Length")
        if length is not None and len(self.body) != int(length):
            raise ValueError("Catalogue byte count differs from declared Content-Length")

    def close(self):
        self.response.close()


class _RecordingClient:
    def __init__(self, session: Any, remaining_budget: int):
        self.session, self.remaining_budget = session, remaining_budget
        self.last_response = None

    def get(self, url: str, **kwargs: Any):
        kwargs["headers"] = {"Accept-Encoding": "identity"}
        self.last_response = _RecordedResponse(self.session.get(url, **kwargs), self.remaining_budget)
        return self.last_response


def probe_noaa_archive(
    output_dir: str | Path,
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
    max_keys: int = 2,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
    include_readme: bool = True,
    session: Any = None,
) -> dict[str, Any]:
    """Save one bounded catalogue page per prefix and optional provider README.

    At most ten prefixes are accepted; pages are never followed automatically.
    Raw response checksums make the evidence reproducible. Forecast objects are
    listed, never implicitly downloaded. `max_keys` applies per prefix.
    """
    byte_budget = _budget(byte_budget)
    if isinstance(prefixes, str):
        raise ValueError("prefixes must be a collection of strings")
    prefixes = list(prefixes)
    if any(not isinstance(p, str) or len(p) > 1024 for p in prefixes):
        raise ValueError("Invalid catalogue prefix")
    if not 1 <= len(prefixes) <= 10 or len(set(prefixes)) != len(prefixes):
        raise ValueError("Provide 1 to 10 distinct catalogue prefixes")
    if isinstance(max_keys, bool) or not isinstance(max_keys, int) or not 1 <= max_keys <= 1000:
        raise ValueError("max_keys must be an integer from 1 to 1000")
    output_dir = Path(output_dir)
    client, transferred, evidence = _client(session), 0, []
    for prefix in prefixes:
        if transferred >= byte_budget:
            raise ValueError("No byte budget remains for catalogue request")
        recorder = _RecordingClient(client, byte_budget - transferred)
        listing = NOAAAIWPCatalog(session=recorder).list_objects(prefix=prefix, max_keys=max_keys)
        response = recorder.last_response
        body = bytes(response.body)
        transferred += len(body)
        digest = hashlib.sha256(body).hexdigest()
        filename = f"{digest}.catalogue.xml"
        _write_atomic(output_dir / filename, body)
        params = {"list-type": 2, "prefix": prefix, "max-keys": max_keys}
        evidence.append({
            "retrieved_at": _now(), "source_url": NOAA_ENDPOINT + "?" + urlencode(params),
            "request_parameters": params, "sha256": digest, "bytes": len(body),
            "local_file": filename, "http_status": response.status_code,
            "content_type": response.headers.get("Content-Type"), "listing": listing,
        })
    documentation = None
    if include_readme:
        if transferred >= byte_budget:
            raise ValueError("No byte budget remains for README request")
        documentation = download_noaa_object("README.txt", output_dir,
                                             byte_budget=byte_budget - transferred, session=client)
        transferred += documentation["bytes"]
    objects = [obj for page in evidence for obj in page["listing"]["objects"]]
    manifest = {
        "schema_version": 1, "created_at": _now(), "data_kind": "real",
        "evidence_kind": "public_archive_readiness", "provider": "NOAA/CIRA AIWP archive",
        "documentation_url": "https://registry.opendata.aws/aiwp/",
        "bytes_transferred": transferred, "byte_budget": byte_budget,
        "catalogue_pages": evidence, "provider_documentation": documentation,
        "listed_object_count": len(objects),
        "forecast_objects_downloaded": 0, "matched_observation_count": 0,
        "historical_publication_time_verified": False, "skill_verified": False,
        "ready_for_training": False,
        "object_download_plan": [{"key": obj["key"], "size_bytes": obj["size_bytes"],
                                  "fits_default_download_budget": obj["size_bytes"] <= DEFAULT_BYTE_BUDGET}
                                 for obj in objects],
        "limitations": [
            "Catalogue existence does not verify meteorological content, completeness, or historical delivery latency.",
            "S3 Last-Modified may reflect regenerated or backfilled forecasts; it is not the original issuance time.",
            "Model checkpoint, initialization source, variables, units and accumulation windows need inspection before canonicalization.",
            "Matched, quality-controlled observations and a held-out evaluation are required before claims of forecast skill.",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> None:
    """Standalone entry point; also callable from the main application CLI."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--output", default="artifacts/public_probe")
    probe.add_argument("--prefix", action="append")
    probe.add_argument("--max-keys", type=int, default=2)
    probe.add_argument("--byte-budget", type=int, default=DEFAULT_BYTE_BUDGET)
    probe.add_argument("--no-readme", action="store_true")
    download = commands.add_parser("download")
    download.add_argument("--key", required=True)
    download.add_argument("--output", default="artifacts/public_probe")
    download.add_argument("--byte-budget", type=int, default=DEFAULT_BYTE_BUDGET)
    download.add_argument("--expected-size", type=int)
    download.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    if args.command == "probe":
        result = probe_noaa_archive(args.output, prefixes=args.prefix or DEFAULT_PREFIXES,
                                    max_keys=args.max_keys, byte_budget=args.byte_budget,
                                    include_readme=not args.no_readme)
        print(json.dumps({k: result[k] for k in ("listed_object_count", "bytes_transferred", "ready_for_training")}, indent=2))
    else:
        result = download_noaa_object(args.key, args.output, byte_budget=args.byte_budget,
                                      expected_size=args.expected_size, expected_sha256=args.expected_sha256)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
