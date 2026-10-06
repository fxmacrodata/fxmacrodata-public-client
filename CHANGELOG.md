# Changelog

## 0.1.2 - 2026-10-06

### Security

- REST requests now send the API key in the documented `X-API-Key` header instead of the
  deprecated `?api_key=` query parameter. This covers `execute`, `history`, `history_path` and
  `stream_events`, so the key no longer appears in URLs, proxy logs or exception text.
- Hosted MCP requests send the key as `Authorization: Bearer <key>`. A key shorter than 16
  characters, or containing whitespace, cannot be read as a Bearer API key by the MCP server, so
  it keeps the legacy query form.
- The key is held in a masked `SecretKey` wrapper, so `repr()`, `vars()` and pickling never
  expose it. It is validated at construction (printable ASCII, trimmed) without being echoed.
  Subclasses that read `self._api_key` still get the string, through a property.
- Credentials are added per request and are never stored in the reusable MCP session headers.
- Transport failures are raised without retaining the underlying exception as `__context__`.
- `X-API-Key`, `Authorization` and `Cookie` are rejected as operation or `history_path`
  arguments.
- A 3xx response raises a clear error stating that redirects are not followed. Redirects were
  already disabled.

### Added

- Optional keyword-only `base_url` and `mcp_url` constructor arguments. Each must be an
  `https://` URL with no credentials, query or fragment, and is validated at construction.
- `fxmacrodata_public.__version__`. The MCP `clientInfo` now reports the real package version.

### Changed

- A 2xx REST response raises `FXMacroDataError` when its body is not a JSON object or array, is
  not JSON, or reports an error (`success: false`, `status: "error"`, an `error`/`errors` value
  without `data`, or a bare `detail`). The remote error text is not echoed. A short lowercase
  error code, such as `subscription_required`, is included when it is safe to show.
- Pagination checks are stricter. Any pagination field the server supplies (`offset`,
  `returned_count`, `limit`, `total_count`, `next_offset`) must be a non-negative integer, and a
  boolean does not count. While `has_more` is true, `next_offset` must advance exactly to
  `offset + returned rows`. A final page may not report a further offset, and a page may not
  extend past `total_count`. A requested `offset` or `limit` that is not a valid integer, or a
  malformed resume checkpoint, is rejected before any request.
- When a history download fails with a client error, the `IncompleteHistoryError` message now
  includes that error's own message, which never contains credentials.

The public API is backward compatible: existing constructor arguments, methods, result shapes and
exception types are unchanged.

## 0.1.1

- Tag fxmacrodata.com links in package metadata and runtime output.
