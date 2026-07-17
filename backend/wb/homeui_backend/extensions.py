#!/usr/bin/env python3

import http.client
import json
import logging
import os
import re
import socket
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import unquote, urlparse

from .http_response import (
    HttpResponse,
    response_200,
    response_400,
    response_401,
    response_403,
    response_404,
)
from .users_storage import UserType

EXTENSION_MANIFEST_DIRS = (
    "/usr/share/wb-mqtt-homeui/extensions.d",
    "/usr/lib/wb-mqtt-homeui/extensions.d",
)
EXTENSION_ASSET_DIRS = (
    "/usr/share/wb-mqtt-homeui/extensions",
    "/usr/lib/wb-mqtt-homeui/extensions",
)

EXTENSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
EXTENSION_ROUTE_RE = re.compile(r"^integrations/[a-z0-9][a-z0-9._/-]{0,126}$")
EXTENSION_ENTRY_RE = re.compile(r"^/extensions/[a-z0-9][a-z0-9._/-]*\.(js|mjs)$")
ALLOWLIST_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
MAX_REQUEST_BODY = 1024 * 1024
MAX_RESPONSE_BODY = 2 * 1024 * 1024
PROXY_TIMEOUT_SECONDS = 5
EXTENSION_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ExtensionAllowlistRule:
    method: str
    path: str

    def matches(self, method: str, path: str) -> bool:
        if self.method != method:
            return False
        if self.path.endswith("/*"):
            return path.startswith(self.path[:-1])
        return self.path == path


@dataclass(frozen=True)
# The manifest mirrors the fixed extension contract, whose eight fields are all required.
# pylint: disable-next=too-many-instance-attributes
class ExtensionManifest:
    id: str
    route: str
    title: dict[str, str]
    entry: str
    socket: str
    api: tuple[ExtensionAllowlistRule, ...]
    minimum_write_role: UserType
    contract_version: int

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "route": self.route,
            "title": self.title,
            "entry": self.entry,
            "minimumWriteRole": self.minimum_write_role.value,
            "contractVersion": self.contract_version,
        }


class ExtensionRegistry:
    def __init__(self, manifests: tuple[ExtensionManifest, ...]):
        self._by_id = {manifest.id: manifest for manifest in manifests}
        self._public = [manifest.public_dict() for manifest in manifests]

    @classmethod
    def load(cls, dirs: tuple[str, ...] = EXTENSION_MANIFEST_DIRS) -> "ExtensionRegistry":
        manifests = []
        seen_ids: set[str] = set()
        seen_routes: set[str] = set()
        for manifest_dir in dirs:
            if not _is_trusted_manifest_dir(manifest_dir):
                logging.warning("Skipping untrusted extension manifest directory %s", manifest_dir)
                continue
            try:
                entries = sorted(os.scandir(manifest_dir), key=lambda entry: entry.name)
            except FileNotFoundError:
                continue
            except OSError as e:
                logging.warning("Skipping extension manifest directory %s: %s", manifest_dir, e)
                continue
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(".json"):
                    continue
                manifest = _load_manifest_file(entry.path)
                if manifest is None:
                    continue
                if manifest.id in seen_ids:
                    logging.error("Skipping duplicate extension id %s from %s", manifest.id, entry.path)
                    continue
                if manifest.route in seen_routes:
                    logging.error("Skipping duplicate extension route %s from %s", manifest.route, entry.path)
                    continue
                seen_ids.add(manifest.id)
                seen_routes.add(manifest.route)
                manifests.append(manifest)
        return cls(tuple(manifests))

    def get(self, extension_id: str) -> Optional[ExtensionManifest]:
        return self._by_id.get(extension_id)

    def get_by_entry(self, entry: str) -> Optional[ExtensionManifest]:
        for manifest in self._by_id.values():
            if manifest.entry == entry:
                return manifest
        return None

    def public_json(self) -> str:
        return json.dumps(self._public, ensure_ascii=False)


def _is_trusted_manifest_dir(path: str) -> bool:
    normalized = os.path.normpath(path)
    return normalized.startswith("/usr/share/") or normalized.startswith("/usr/lib/")


def _load_manifest_file(path: str) -> Optional[ExtensionManifest]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return validate_manifest(data)
    except (OSError, ValueError, TypeError) as e:
        logging.error("Skipping invalid extension manifest %s: %s", path, e)
        return None


def validate_manifest(data: object) -> ExtensionManifest:
    if not isinstance(data, dict):
        raise TypeError("manifest must be an object")
    extension_id = _required_str(data, "id")
    if not EXTENSION_ID_RE.fullmatch(extension_id):
        raise ValueError("invalid id")
    route = _required_str(data, "route").strip("/")
    if not EXTENSION_ROUTE_RE.fullmatch(route) or ".." in route.split("/"):
        raise ValueError("route must be under integrations/")
    title = data.get("title")
    if not isinstance(title, dict) or not all(isinstance(title.get(lang), str) for lang in ("ru", "en")):
        raise TypeError("title.ru and title.en are required")
    entry = _required_str(data, "entry")
    if not EXTENSION_ENTRY_RE.fullmatch(entry) or ".." in entry.split("/"):
        raise ValueError("entry must be a same-origin /extensions/*.js asset")
    unix_socket = _required_str(data, "socket")
    if not unix_socket.startswith("/") or "://" in unix_socket or ".." in unix_socket.split("/"):
        raise ValueError("socket must be an absolute Unix socket path")
    minimum_write_role = UserType(data.get("minimum_write_role", UserType.ADMIN.value))
    contract_version = data.get("contract_version")
    if contract_version != EXTENSION_CONTRACT_VERSION:
        raise ValueError(f"contract_version must be {EXTENSION_CONTRACT_VERSION}")
    api_data = data.get("api")
    if not isinstance(api_data, list) or not api_data:
        raise TypeError("api must be a non-empty list")
    return ExtensionManifest(
        id=extension_id,
        route=route,
        title={"ru": title["ru"], "en": title["en"]},
        entry=entry,
        socket=unix_socket,
        api=tuple(_validate_allowlist_rule(rule) for rule in api_data),
        minimum_write_role=minimum_write_role,
        contract_version=contract_version,
    )


def _required_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _validate_allowlist_rule(rule: object) -> ExtensionAllowlistRule:
    if not isinstance(rule, dict):
        raise TypeError("api rule must be an object")
    method = _required_str(rule, "method").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("unsupported api method")
    path = _required_str(rule, "path")
    if not ALLOWLIST_PATH_RE.fullmatch(path) or "/../" in path or path.startswith("/../"):
        raise ValueError("invalid api path")
    return ExtensionAllowlistRule(method, path)


def get_extension_path(request_path: str) -> tuple[Optional[str], Optional[str]]:
    path = urlparse(request_path).path
    prefix = "/api/extensions/"
    if not path.startswith(prefix):
        return None, None
    rest = path[len(prefix) :]
    extension_id, _, proxied = rest.partition("/")
    if not extension_id or not proxied:
        return None, None
    proxied_path = "/" + unquote(proxied)
    if (
        not ALLOWLIST_PATH_RE.fullmatch(proxied_path)
        or "/../" in proxied_path
        or proxied_path.startswith("/../")
    ):
        return None, None
    return extension_id, proxied_path


def extensions_manifest_handler(
    _request: BaseHTTPRequestHandler, registry: ExtensionRegistry
) -> HttpResponse:
    return response_200([["Content-type", "application/json"]], registry.public_json())


def extension_asset_handler(
    request: BaseHTTPRequestHandler, registry: ExtensionRegistry
) -> HttpResponse:
    if "://" in request.path:
        return response_404()
    entry = urlparse(request.path).path
    if not _is_safe_extension_entry_path(entry):
        return response_404()
    if registry.get_by_entry(entry) is None:
        return response_404()
    asset_path = _find_extension_asset(entry)
    if asset_path is None:
        return response_404()
    try:
        with open(asset_path, "r", encoding="utf-8") as fp:
            body = fp.read()
    except (OSError, UnicodeDecodeError):
        logging.warning("Failed to read extension asset %s", asset_path, exc_info=True)
        return response_404()
    return response_200(
        [
            ["Content-type", _extension_asset_content_type(entry)],
            ["Cache-Control", "no-cache"],
            ["X-Content-Type-Options", "nosniff"],
        ],
        body,
    )


def _is_safe_extension_entry_path(path: str) -> bool:
    return EXTENSION_ENTRY_RE.fullmatch(path) is not None and ".." not in path.split("/")


def _find_extension_asset(entry: str) -> Optional[str]:
    relative_path = entry.removeprefix("/extensions/")
    for asset_dir in EXTENSION_ASSET_DIRS:
        if not _is_trusted_manifest_dir(asset_dir):
            logging.warning("Skipping untrusted extension asset directory %s", asset_dir)
            continue
        base_dir = os.path.realpath(asset_dir)
        candidate = os.path.realpath(os.path.join(base_dir, relative_path))
        if not candidate.startswith(base_dir + os.sep):
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def _extension_asset_content_type(entry: str) -> str:
    if entry.endswith(".mjs"):
        return "text/javascript"
    return "application/javascript"


# pylint: disable-next=too-many-return-statements
def extension_proxy_handler(
    request: BaseHTTPRequestHandler,
    registry: ExtensionRegistry,
    role: Optional[UserType],
) -> HttpResponse:
    extension_id, proxied_path = get_extension_path(request.path)
    if extension_id is None or proxied_path is None:
        return response_404()
    manifest = registry.get(extension_id)
    if manifest is None:
        return response_404()
    if not any(rule.matches(request.command, proxied_path) for rule in manifest.api):
        return response_403()
    if role is None:
        return response_401()
    if request.command != "GET" and not _role_allows(role, manifest.minimum_write_role):
        return response_403()
    body, error = _read_bounded_body(request)
    if error is not None:
        return error
    return _proxy_to_socket(request, manifest, proxied_path, body, role)

def _role_allows(actual: UserType, required: UserType) -> bool:
    order = {UserType.USER: 1, UserType.OPERATOR: 2, UserType.ADMIN: 3}
    return order[actual] >= order[required]


def _read_bounded_body(request: BaseHTTPRequestHandler) -> tuple[bytes, Optional[HttpResponse]]:
    try:
        length = int(request.headers.get("Content-Length", "0"))
    except ValueError:
        return b"", response_400("invalid content length")
    if length > MAX_REQUEST_BODY:
        return b"", HttpResponse(413, body="Request body too large")
    body = request.rfile.read(length) if length else b""
    if len(body) > MAX_REQUEST_BODY:
        return b"", HttpResponse(413, body="Request body too large")
    return body, None


def _proxy_to_socket(
    request: BaseHTTPRequestHandler,
    manifest: ExtensionManifest,
    proxied_path: str,
    body: bytes,
    role: UserType,
) -> HttpResponse:
    connection = UnixSocketHTTPConnection(manifest.socket, timeout=PROXY_TIMEOUT_SECONDS)
    try:
        connection.request(
            request.command,
            proxied_path,
            body=body,
            headers=_proxy_headers(request, manifest.id, role, len(body)),
        )
        response = connection.getresponse()
        response_body = response.read(MAX_RESPONSE_BODY + 1)
        if response.status >= 500:
            return HttpResponse(502, body="Extension API unavailable")
        if len(response_body) > MAX_RESPONSE_BODY:
            return HttpResponse(502, body="Extension response too large")
        content_type = response.getheader("Content-Type", "application/json")
        return HttpResponse(
            response.status,
            headers=[
                ["Content-type", content_type],
                ["Cache-Control", "no-store"],
                ["X-Content-Type-Options", "nosniff"],
            ],
            body=response_body.decode("utf-8", errors="replace"),
        )
    except (OSError, http.client.HTTPException, socket.timeout):
        logging.warning("Extension %s API request failed", manifest.id, exc_info=True)
        return HttpResponse(502, body="Extension API unavailable")
    finally:
        connection.close()


def _proxy_headers(
    request: BaseHTTPRequestHandler, extension_id: str, role: UserType, content_length: int
) -> dict[str, str]:
    headers = {
        "Host": "extension.local",
        "Content-Length": str(content_length),
        "X-Homeui-Extension-Id": extension_id,
        "X-Homeui-User-Role": role.value,
        "X-Homeui-Extension-Contract": str(EXTENSION_CONTRACT_VERSION),
    }
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type
    request_id = request.headers.get("X-Request-Id")
    if request_id:
        headers["X-Request-Id"] = request_id[:128]
    return headers


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock
