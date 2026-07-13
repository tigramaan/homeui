# Trusted HomeUI Extensions

HomeUI can load trusted, package-installed React pages under the existing authenticated
application without replacing vendor frontend files. Extensions are intended for local Debian
packages. They are not a general plugin marketplace and cannot be registered from user-provided
or network URLs.

## Manifest

Install one JSON manifest per extension into one of the package-managed directories:

- `/usr/share/wb-mqtt-homeui/extensions.d`
- `/usr/lib/wb-mqtt-homeui/extensions.d`

Manifests from `/etc`, `/var`, user home directories, or network locations are ignored.

```json
{
  "id": "umec-mqtt-v3",
  "route": "integrations/umec-mqtt-v3",
  "title": {
    "ru": "UMEC MQTT v3",
    "en": "UMEC MQTT v3"
  },
  "entry": "/extensions/umec-mqtt-v3/entry.js",
  "socket": "/run/umec-mqtt-v3/homeui.sock",
  "api": [
    { "method": "GET", "path": "/status" },
    { "method": "POST", "path": "/config" }
  ],
  "minimum_write_role": "admin"
}
```

Fields:

- `id`: stable lowercase id. It is used in `/api/extensions/<id>/...`.
- `route`: must be under `integrations/`. The frontend hosts only this route.
- `title.ru`, `title.en`: navigation/page titles.
- `entry`: same-origin JavaScript module asset. Absolute URLs and path traversal are rejected.
- `socket`: absolute Unix socket path for the local extension API. TCP and URLs are rejected.
- `api`: allowlist of method/path pairs. A path ending with `/*` allows that local prefix.
- `minimum_write_role`: role required for non-GET requests. Use `admin`.

Duplicate ids or routes are skipped. Malformed manifests are skipped and logged.

## Frontend Module API

The `entry` module must export a default React component:

```ts
import type { ExtensionModuleContext } from "homeui/extensions";

export default function UmecPage({ api, i18n, theme, role, isAdmin }: ExtensionModuleContext) {
  // Render a full page here.
}
```

The context is intentionally narrow:

- `api`: authenticated same-origin client scoped to `/api/extensions/<id>`.
- `i18n`: HomeUI i18n instance.
- `theme`: current HomeUI theme id.
- `role`: current HomeUI role, if available.
- `isAdmin`: convenience boolean for admin-only controls.

The extension module cannot register arbitrary HomeUI routes. HomeUI only mounts it at the
manifest route.

## Backend Proxy API

Browser calls must use the provided `api` client or same-origin URLs below
`/api/extensions/<id>/...`. The HomeUI backend:

- accepts only registered extension ids;
- rejects traversal and cross-extension paths;
- checks the manifest method/path allowlist before proxying;
- requires authentication for reads;
- requires the manifest write role for non-GET requests;
- proxies only to the manifest Unix socket;
- forwards sanitized `X-Homeui-Extension-Id` and `X-Homeui-User-Role` headers;
- bounds request body, response body, and socket timeout;
- redacts socket/upstream errors.

No browser bearer token, TCP proxy, arbitrary socket, or arbitrary URL access is exposed.

Legacy compatibility: on systems where HomeUI users are not configured and nginx has already
authenticated the request using legacy HTTP auth, the backend treats that request as `admin` at
this compatibility boundary only. Role-bearing HomeUI sessions and nginx `Wb-User-Type` headers
take precedence whenever available.
