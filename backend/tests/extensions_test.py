import io
import json
import os
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from wb.homeui_backend.extensions import (
    MAX_REQUEST_BODY,
    ExtensionRegistry,
    extension_asset_handler,
    extension_proxy_handler,
    get_extension_path,
    validate_manifest,
)
from wb.homeui_backend.http_response import response_401, response_403, response_404
from wb.homeui_backend.users_storage import UserType


def manifest(**overrides):
    data = {
        "id": "umec",
        "route": "integrations/umec",
        "title": {"ru": "УМЭК", "en": "UMEC"},
        "entry": "/extensions/umec/entry.js",
        "socket": "/run/umec.sock",
        "api": [{"method": "GET", "path": "/status"}, {"method": "POST", "path": "/apply"}],
        "minimum_write_role": "admin",
        "contract_version": 1,
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
            manifest(contract_version=2),
            manifest(contract_version=None),
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


class ExtensionAssetHandlerTest(unittest.TestCase):
    def request(self, path="/extensions/umec/entry.js"):
        req = MagicMock()
        req.path = path
        return req

    def registry(self, **overrides):
        return ExtensionRegistry((validate_manifest(manifest(**overrides)),))

    def test_serves_registered_entry_from_trusted_asset_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(f"{temp_dir}/umec")
            with open(f"{temp_dir}/umec/entry.js", "w", encoding="utf-8") as fp:
                fp.write("export default function Extension() { return null; }\n")

            with (
                patch("wb.homeui_backend.extensions.EXTENSION_ASSET_DIRS", (temp_dir,)),
                patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True),
            ):
                response = extension_asset_handler(self.request(), self.registry())

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, "export default function Extension() { return null; }\n")
        self.assertIn(["Content-type", "application/javascript"], response.headers)
        self.assertIn(["Cache-Control", "no-cache"], response.headers)
        self.assertIn(["X-Content-Type-Options", "nosniff"], response.headers)

    def test_manifest_entry_is_fetchable(self):
        registry = self.registry()
        entry = json.loads(registry.public_json())[0]["entry"]
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(f"{temp_dir}/umec")
            with open(f"{temp_dir}/umec/entry.js", "w", encoding="utf-8") as fp:
                fp.write("export default function Extension() {}\n")

            with (
                patch("wb.homeui_backend.extensions.EXTENSION_ASSET_DIRS", (temp_dir,)),
                patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True),
            ):
                response = extension_asset_handler(self.request(entry), registry)

        self.assertEqual(response.status, 200)

    def test_missing_registered_entry_returns_404(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("wb.homeui_backend.extensions.EXTENSION_ASSET_DIRS", (temp_dir,)),
                patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True),
            ):
                response = extension_asset_handler(self.request(), self.registry())

        self.assertEqual(response, response_404())

    def test_traversal_is_rejected(self):
        response = extension_asset_handler(
            self.request("/extensions/umec/%2E%2E/secret.js"), self.registry()
        )
        self.assertEqual(response, response_404())

    def test_url_request_target_is_rejected(self):
        response = extension_asset_handler(
            self.request("https://example.test/extensions/umec/entry.js"), self.registry()
        )
        self.assertEqual(response, response_404())

    def test_existing_undeclared_asset_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(f"{temp_dir}/umec")
            with open(f"{temp_dir}/umec/secret.js", "w", encoding="utf-8") as fp:
                fp.write("secret")

            with (
                patch("wb.homeui_backend.extensions.EXTENSION_ASSET_DIRS", (temp_dir,)),
                patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True),
            ):
                response = extension_asset_handler(
                    self.request("/extensions/umec/secret.js"), self.registry()
                )

        self.assertEqual(response, response_404())

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as secret_dir:
            os.symlink(secret_dir, f"{temp_dir}/umec")
            with open(f"{secret_dir}/entry.js", "w", encoding="utf-8") as fp:
                fp.write("secret")

            with (
                patch("wb.homeui_backend.extensions.EXTENSION_ASSET_DIRS", (temp_dir,)),
                patch("wb.homeui_backend.extensions._is_trusted_manifest_dir", return_value=True),
            ):
                response = extension_asset_handler(self.request(), self.registry())

        self.assertEqual(response, response_404())


class ExtensionProxyGuardTest(unittest.TestCase):
    def setUp(self):
        self.registry = ExtensionRegistry((validate_manifest(manifest()),))

    def test_path_traversal_is_rejected(self):
        self.assertEqual(get_extension_path("/api/extensions/umec/../other/status"), (None, None))

    def test_unknown_id_is_not_proxied(self):
        response = extension_proxy_handler(
            request("/api/extensions/unknown/status"), self.registry, UserType.ADMIN
        )
        self.assertEqual(response, response_404())

    def test_cross_extension_access_is_denied_by_path_validation(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/%2E%2E/other/status"),
            self.registry,
            UserType.ADMIN,
        )
        self.assertEqual(response, response_404())

    def test_unauthenticated_request_is_denied(self):
        response = extension_proxy_handler(request(), self.registry, None)
        self.assertEqual(response, response_401())

    def test_insufficient_role_for_write_is_denied(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/apply", method="POST", body=b"{}"),
            self.registry,
            UserType.USER,
        )
        self.assertEqual(response, response_403())

    def test_method_path_denial(self):
        response = extension_proxy_handler(
            request("/api/extensions/umec/status", method="POST", role=UserType.ADMIN, body=b"{}"),
            self.registry,
            None,
        )
        self.assertEqual(response, response_403())

    def test_oversized_body_is_rejected_before_proxy(self):
        req = request("/api/extensions/umec/apply", method="POST", role=UserType.ADMIN)
        req.headers = {"Content-Length": str(MAX_REQUEST_BODY + 1)}
        response = extension_proxy_handler(req, self.registry, UserType.ADMIN)
        self.assertEqual(response.status, 413)

    def test_missing_resolved_role_is_denied(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            response = extension_proxy_handler(
                request("/api/extensions/umec/apply", method="POST", body=b"{}"),
                self.registry,
                None,
            )
        self.assertEqual(response, response_401())
        connection_class.assert_not_called()

    def test_forged_role_header_is_ignored_in_favor_of_resolved_role(self):
        req = request("/api/extensions/umec/apply", method="POST", body=b"{}")
        req.headers["Wb-User-Type"] = "admin"
        response = extension_proxy_handler(req, self.registry, UserType.USER)
        self.assertEqual(response, response_403())

    def test_socket_timeout_is_redacted(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            connection_class.return_value.request.side_effect = socket.timeout("/run/umec.sock timed out")
            response = extension_proxy_handler(request(), self.registry, UserType.ADMIN)
        self.assertEqual(response.status, 502)
        self.assertNotIn("/run/umec.sock", response.body)

    def test_upstream_5xx_body_is_redacted(self):
        with patch("wb.homeui_backend.extensions.UnixSocketHTTPConnection") as connection_class:
            connection = connection_class.return_value
            upstream = MagicMock()
            upstream.status = 500
            upstream.read.return_value = b"secret stack trace /run/umec.sock"
            connection.getresponse.return_value = upstream
            response = extension_proxy_handler(request(), self.registry, UserType.ADMIN)
        self.assertEqual(response.status, 502)
        self.assertNotIn("secret", response.body)
