import io
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from wb.homeui_backend.extensions import (
    MAX_REQUEST_BODY,
    ExtensionRegistry,
    extension_proxy_handler,
    get_extension_path,
    validate_manifest,
)
from wb.homeui_backend.http_response import response_401, response_403, response_404
from wb.homeui_backend.sessions_storage import Session
from wb.homeui_backend.users_storage import User, UserType


def manifest(**overrides):
    data = {
        "id": "umec",
        "route": "integrations/umec",
        "title": {"ru": "УМЭК", "en": "UMEC"},
        "entry": "/extensions/umec/entry.js",
        "socket": "/run/umec.sock",
        "api": [{"method": "GET", "path": "/status"}, {"method": "POST", "path": "/apply"}],
        "minimum_write_role": "admin",
    }
    data.update(overrides)
    return data


def request(path="/api/extensions/umec/status", method="GET", role=None, body=b""):
    req = MagicMock()
    req.path = path
    req.command = method
    req.headers = {"Content-Length": str(len(body))}
    if role:
        req.headers["Wb-User-Type"] = role.value
    req.rfile = io.BytesIO(body)
    return req


def session(role):
    return Session("s1", User("u1", "user", "hash", role, False), datetime.now(timezone.utc))


class ExtensionManifestValidationTest(unittest.TestCase):
    def test_valid_manifest(self):
        parsed = validate_manifest(manifest())
        self.assertEqual(parsed.id, "umec")
        self.assertEqual(parsed.route, "integrations/umec")
        self.assertEqual(parsed.entry, "/extensions/umec/entry.js")

    def test_malformed_manifests_are_rejected(self):
        bad_cases = [
            manifest(id="../umec"),
            manifest(route="settings/umec"),
            manifest(route="integrations/../umec"),
            manifest(entry="https://example.test/entry.js"),
            manifest(entry="/extensions/../entry.js"),
            manifest(socket="http://127.0.0.1:9000"),
            manifest(api=[{"method": "GET", "path": "/../secret"}]),
            manifest(api=[{"method": "TRACE", "path": "/status"}]),
            {"id": "umec"},
        ]
        for data in bad_cases:
            with self.subTest(data=data):
                with self.assertRaises((TypeError, ValueError)):
                    validate_manifest(data)

    def test_registry_skips_duplicate_ids_and_routes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = manifest(id="umec", route="integrations/umec")
            duplicate_id = manifest(id="umec", route="integrations/other")
            duplicate_route = manifest(id="other", route="integrations/umec")
            valid_other = manifest(id="other", route="integrations/other")
            for name, data in {
                "1.json": first,
                "2.json": duplicate_id,
                "3.json": duplicate_route,
                "4.json": valid_other,
            }.items():
                with open(f"{temp_dir}/{name}", "w", encoding="utf-8") as fp:
                    json.dump(data, fp)
            with patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True):
                registry = ExtensionRegistry.load((temp_dir,))
        public = json.loads(registry.public_json())
        self.assertEqual([item["id"] for item in public], ["umec", "other"])

    def test_untrusted_directory_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(f"{temp_dir}/umec.json", "w", encoding="utf-8") as fp:
                json.dump(manifest(), fp)
            registry = ExtensionRegistry.load((temp_dir,))
        self.assertEqual(json.loads(registry.public_json()), [])


class ExtensionProxyGuardTest(unittest.TestCase):
    def setUp(self):
        self.registry = ExtensionRegistry((validate_manifest(manifest()),))

    def test_path_traversal_is_rejected(self):
        self.assertEqual(get_extension_path("/api/extensions/umec/../other/status"), (None, None))

    def test_unknown_id_is_not_proxied(self):
        response = extension_proxy_handler(
            request("/api/extensions/unknown/status"), self.registry, session(UserType.ADMIN), True
        )
        self.assertEqual(response, response_404())

    def test_cross_extension_access_is_denied_by_path_validation(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/%2E%2E/other/status"),
            self.registry,
            session(UserType.ADMIN),
            True,
        )
        self.assertEqual(response, response_404())

    def test_unauthenticated_request_is_denied(self):
        response = extension_proxy_handler(request(), self.registry, None, True)
        self.assertEqual(response, response_401())

    def test_insufficient_role_for_write_is_denied(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/apply", method="POST", role=UserType.USER, body=b"{}"),
            self.registry,
            None,
            True,
        )
        self.assertEqual(response, response_403())

    def test_method_path_denial(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/status", method="POST", role=UserType.ADMIN, body=b"{}"),
            self.registry,
            None,
            True,
        )
        self.assertEqual(response, response_403())

    def test_oversized_body_is_rejected_before_proxy(self):
        req = request("/api/extensions/umec/apply", method="POST", role=UserType.ADMIN)
        req.headers = {"Content-Length": str(MAX_REQUEST_BODY + 1), "Wb-User-Type": "admin"}
        response = extension_proxy_handler(req, self.registry, None, True)
        self.assertEqual(response.status, 413)

    def test_legacy_no_role_context_is_admin_when_no_homeui_users_exist(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            connection = connection_class.return_value
            upstream = MagicMock()
            upstream.status = 204
            upstream.read.return_value = b""
            upstream.getheader.return_value = "application/json"
            connection.getresponse.return_value = upstream
            response = extension_proxy_handler(
                request("/api/extensions/umec/apply", method="POST", body=b"{}"),
                self.registry,
                None,
                False,
            )
        self.assertEqual(response.status, 204)
        connection.request.assert_called_once()
        self.assertEqual(connection.request.call_args.kwargs["headers"]["X-Homeui-User-Role"], "admin")

    def test_socket_timeout_is_redacted(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            connection_class.return_value.request.side_effect = socket.timeout("/run/umec.sock timed out")
            response = extension_proxy_handler(request(role=UserType.ADMIN), self.registry, None, True)
        self.assertEqual(response.status, 502)
        self.assertNotIn("/run/umec.sock", response.body)

    def test_upstream_5xx_body_is_redacted(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            connection = connection_class.return_value
            upstream = MagicMock()
            upstream.status = 500
            upstream.read.return_value = b"secret stack trace /run/umec.sock"
            connection.getresponse.return_value = upstream
            response = extension_proxy_handler(request(role=UserType.ADMIN), self.registry, None, True)
        self.assertEqual(response.status, 502)
        self.assertNotIn("secret", response.body)
