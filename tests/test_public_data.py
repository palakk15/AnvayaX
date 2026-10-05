"""No external requests: preserve evidence bytes and enforce transfer budgets."""
from __future__ import annotations

import hashlib
import json

import pytest

from bharatblend.public_data import NOAA_ENDPOINT, download_noaa_object, probe_noaa_archive


class Response:
    def __init__(self, body=b"sample", status=200, headers=None, chunks=None):
        self.body, self.status_code = body, status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self.chunks = chunks
        self.closed = False
        self.body_read = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError("HTTP error")

    def iter_content(self, chunk_size):
        self.body_read = True
        yield from self.chunks if self.chunks is not None else [self.body]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


def catalogue(prefix="GRAP/", size=9_000_000_000, truncated="false"):
    return f'''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<IsTruncated>{truncated}</IsTruncated><NextContinuationToken>next-page</NextContinuationToken>
<Contents><Key>{prefix}forecast.nc</Key><Size>{size}</Size>
<LastModified>2025-01-01T00:00:00Z</LastModified><ETag>"multipart-1113"</ETag></Contents>
</ListBucketResult>'''.encode()


def test_download_exact_bytes_receipt_and_known_checksum(tmp_path):
    body = b"provider evidence\x00\n"
    digest = hashlib.sha256(body).hexdigest()
    response = Response(body, headers={"Content-Length": str(len(body)), "ETag": '"multi-3"'})
    session = Session(response)
    receipt = download_noaa_object("README.txt", tmp_path, expected_size=len(body),
                                   expected_sha256=digest.upper(), session=session)
    assert receipt["sha256"] == digest
    assert (tmp_path / receipt["local_file"]).read_bytes() == body
    assert json.loads((tmp_path / f"{digest}.receipt.json").read_text()) == receipt
    assert receipt["data_kind"] == "real"
    assert receipt["evidence_kind"] == "provider_documentation"
    assert receipt["historical_publication_time_verified"] is False
    assert receipt["skill_verified"] is False
    assert response.closed
    url, kwargs = session.requests[0]
    assert url == NOAA_ENDPOINT + "README.txt"
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
    assert kwargs["headers"] == {"Accept-Encoding": "identity"}


def test_known_large_object_is_rejected_without_http(tmp_path):
    session = Session()
    with pytest.raises(ValueError, match="Expected catalogue size exceeds"):
        download_noaa_object("GRAP/global.nc", tmp_path, expected_size=9_333_733_097, session=session)
    assert session.requests == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("response,message", [
    (Response(status=302), "complete HTTP 200"),
    (Response(status=206), "complete HTTP 200"),
    (Response(headers={"Content-Length": "100"}), "Content-Length exceeds"),
    (Response(headers={"Content-Length": "-1"}), "Content-Length exceeds"),
    (Response(headers={"Content-Encoding": "gzip"}), "Compressed HTTP"),
])
def test_rejected_headers_never_read_the_body(tmp_path, response, message):
    with pytest.raises(ValueError, match=message):
        download_noaa_object("README.txt", tmp_path, byte_budget=10, session=Session(response))
    assert response.closed and not response.body_read
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("response,kwargs,message", [
    (Response(headers={}, chunks=[b"1234", b"5678"]), {"byte_budget": 6}, "Stream exceeds"),
    (Response(b"123", headers={"Content-Length": "4"}), {}, "byte count differs"),
    (Response(b"123", headers={}), {"expected_size": 4}, "byte count differs"),
    (Response(b"123"), {"expected_sha256": "0" * 64}, "SHA-256 differs"),
])
def test_failed_transfer_leaves_no_partial_data(tmp_path, response, kwargs, message):
    with pytest.raises(ValueError, match=message):
        download_noaa_object("README.txt", tmp_path, session=Session(response), **kwargs)
    assert response.closed
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("key", ["../secret", "/README.txt", "a/../b", "a\\b", "", "a\nb", "https://foreign.test/file"])
def test_keys_cannot_be_arbitrary_urls_or_paths(tmp_path, key):
    session = Session()
    with pytest.raises(ValueError, match="relative NOAA object key"):
        download_noaa_object(key, tmp_path, session=session)
    assert not session.requests


def test_probe_preserves_raw_catalogue_and_never_follows_pages(tmp_path):
    xml = catalogue(truncated="true")
    readme = b"Original provider documentation\n"
    session = Session(Response(xml), Response(readme))
    result = probe_noaa_archive(tmp_path, prefixes=["GRAP/"], max_keys=1, session=session)
    page = result["catalogue_pages"][0]
    assert (tmp_path / page["local_file"]).read_bytes() == xml
    assert page["sha256"] == hashlib.sha256(xml).hexdigest()
    assert result["bytes_transferred"] == len(xml) + len(readme)
    assert page["listing"]["truncated"] is True
    assert result["listed_object_count"] == 1
    assert result["forecast_objects_downloaded"] == 0
    assert result["matched_observation_count"] == 0
    assert result["ready_for_training"] is False
    assert result["skill_verified"] is False
    assert result["object_download_plan"][0]["fits_default_download_budget"] is False
    assert len(session.requests) == 2
    assert json.loads((tmp_path / "manifest.json").read_text()) == result


def test_probe_combined_budget_applies_across_requests(tmp_path):
    xml = catalogue()
    response = Response(b"documentation beyond remaining budget")
    session = Session(Response(xml), response)
    with pytest.raises(ValueError, match="Content-Length exceeds"):
        probe_noaa_archive(tmp_path, prefixes=["GRAP/"], byte_budget=len(xml) + 5, session=session)
    assert response.closed and not response.body_read
    assert not (tmp_path / "manifest.json").exists()
    assert not list(tmp_path.glob(".download-*"))


def test_probe_without_documentation_is_explicit(tmp_path):
    session = Session(Response(catalogue()))
    result = probe_noaa_archive(tmp_path, prefixes=["GRAP/"], include_readme=False, session=session)
    assert result["provider_documentation"] is None
    assert len(session.requests) == 1


@pytest.mark.parametrize("prefixes", ["GRAP/", [], ["same/", "same/"], [[]], [str(i) for i in range(11)]])
def test_probe_validation_precedes_network(tmp_path, prefixes):
    session = Session()
    with pytest.raises(ValueError):
        probe_noaa_archive(tmp_path, prefixes=prefixes, session=session)
    assert not session.requests
