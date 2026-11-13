"""Minimal HTTP client for the SceneMux Studio API.

Stdlib only (urllib) — the plugin deliberately has zero third-party Python
dependencies. Multipart uploads are built by hand.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
HEALTH_TIMEOUT = 4

JsonBody = Union[Dict[str, Any], list, str]


class SceneMuxError(Exception):
    """Actionable error from transport or the SceneMux server."""


def resolve_base_url(explicit: str = "") -> str:
    return (explicit or DEFAULT_BASE_URL).rstrip("/")


def _request(
    url: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    files: Optional[Dict[str, Tuple[str, bytes]]] = None,
    fields: Optional[dict] = None,
    timeout: float = READ_TIMEOUT,
) -> Tuple[int, JsonBody]:
    """Perform one HTTP request. Returns (status, parsed body).

    Raises SceneMuxError for unreachable hosts and timeouts (with
    actionable, human-readable text). HTTP error statuses are returned,
    not raised, so handlers can report server-side messages verbatim.
    """
    headers = {}
    if files is not None:
        data, content_type = _encode_multipart(fields or {}, files)
        headers["Content-Type"] = content_type
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        data = None

    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse_body(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_body(e.read() or b"")
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        raise SceneMuxError(
            f"Cannot reach SceneMux server at {url} ({reason}). "
            "Is it running? Run scenemux_setup, or set the plugin's base_url setting "
            "if the server runs elsewhere."
        ) from e
    except TimeoutError:
        raise SceneMuxError(
            f"Timed out after {timeout:g}s talking to {url}. "
            "Long operations (renders) should be checked with scenemux_status, not one blocking call."
        ) from None


def get_json(base: str, path: str, timeout: float = READ_TIMEOUT) -> JsonBody:
    status, body = _request(base + path, "GET", timeout=timeout)
    if status >= 400:
        raise SceneMuxError(_format_http_error("GET", path, status, body))
    return body


def post_json(base: str, path: str, payload: dict, timeout: float = READ_TIMEOUT) -> JsonBody:
    status, body = _request(base + path, "POST", payload=payload, timeout=timeout)
    if status >= 400:
        raise SceneMuxError(_format_http_error("POST", path, status, body))
    return body


def upload_file(
    base: str,
    path: str,
    field: str,
    filename: str,
    data: bytes,
    fields: Optional[dict] = None,
    timeout: float = 120,
) -> JsonBody:
    files = {field: (filename, data)}
    status, body = _request(base + path, "POST", files=files, fields=fields, timeout=timeout)
    if status >= 400:
        raise SceneMuxError(_format_http_error("POST", path, status, body))
    return body


def upload_files(
    base: str,
    path: str,
    field: str,
    items: list,
    fields: Optional[dict] = None,
    timeout: float = 300,
) -> JsonBody:
    """Upload several files under one repeated multipart field (e.g. ``files``)."""
    status, body = _request(
        base + path,
        "POST",
        files=[(field, (name, data)) for name, data in items],
        fields=fields,
        timeout=timeout,
    )
    if status >= 400:
        raise SceneMuxError(_format_http_error("POST", path, status, body))
    return body


def download_file(base: str, path: str, dest: Path, timeout: float = 900) -> int:
    """Stream a GET response to a local file. Returns the byte count."""
    url = base + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise SceneMuxError(_format_http_error("GET", path, resp.status, "stream"))
            total = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            return total
    except urllib.error.HTTPError as e:
        raise SceneMuxError(_format_http_error("GET", path, e.code, _parse_body(e.read() or b""))) from e
    except urllib.error.URLError as e:
        raise SceneMuxError(f"Cannot reach SceneMux server at {url} ({e.reason})") from e


def _format_http_error(method: str, path: str, status: int, body: JsonBody) -> str:
    detail = body if isinstance(body, str) else json.dumps(body)[:800]
    hint = ""
    if status in (401, 403):
        hint = " (check that the required API keys are set)"
    return f"{method} {path} -> HTTP {status}{hint}: {detail}"


def _parse_body(body: bytes) -> JsonBody:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return body.decode("utf-8", "replace")[:4000]


def _encode_multipart(fields: dict, files) -> Tuple[bytes, str]:
    """``files`` is a dict of field->(filename, bytes) or a list of
    (field, (filename, bytes)) tuples (lists allow repeated field names)."""
    if isinstance(files, dict):
        file_parts = list(files.items())
    else:
        file_parts = list(files)
    boundary = "scenemux" + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.extend([
            f"--{boundary}".encode("ascii"),
            f'Content-Disposition: form-data; name="{key}"'.encode("ascii"),
            b"",
            str(value).encode("utf-8"),
        ])
    for key, (filename, data) in file_parts:
        parts.extend([
            f"--{boundary}".encode("ascii"),
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"'.encode("utf-8"),
            b"Content-Type: application/octet-stream",
            b"",
            data,
        ])
    parts.append(f"--{boundary}--".encode("ascii"))
    parts.append(b"")
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"
