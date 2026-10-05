# FXMacroData public integration client

Connect Python applications and native integrations to FXMacroData's official macroeconomic history, release calendars and market data through one REST and MCP client. Results retain the original response and provide an additional tabular view.

[Subscribe to FXMacroData](https://fxmacrodata.com/subscribe?utm_source=github&utm_medium=referral&utm_campaign=fxmacrodata-public-client&utm_content=subscribe) for non-USD indicator history, full available history, FX, commodities and positioning. Use the public USD workflow to evaluate the integration before connecting your subscription.

The packaged discovery snapshot includes 29 documented REST operations and 50 hosted MCP tools. MCP discovery can refresh the tool inventory. The research-panel operation accepts its documented JSON request as `body`; all other REST arguments retain their documented parameter names.

Read [FXMacroData documentation](https://fxmacrodata.com/documentation/reference?utm_source=github&utm_medium=referral&utm_campaign=fxmacrodata-public-client&utm_content=docs) or visit [FXMacroData](https://fxmacrodata.com/?utm_source=github&utm_medium=referral&utm_campaign=fxmacrodata-public-client&utm_content=readme).

Install from the package directory or its built wheel:

```sh
python -m pip install .
```

```python
from fxmacrodata_public import FXMacroDataClient

with FXMacroDataClient() as client:
    result = client.execute("release_calendar", {"currency": "usd"})
    rows = result.records()
    original_response = result.payload
```

For evaluation, the USD catalogue, recent macro history and calendar work without an API key. Anonymous indicator history currently covers a rolling 90-day window. Set `FXMACRODATA_API_KEY` or pass `api_key` to the constructor to connect your subscription for protected data access. Credentials are excluded from operation schemas and errors. Requests use documented query-parameter authentication, without following redirects. There are no tracking calls; attribution parameters appear only on website links.

The client redacts credential echoes in results and its requests/urllib3 diagnostics. Its own HTTPS connections keep wire debugging disabled even when the application enables the global `http.client` debug flag. Injected sessions remain the caller's responsibility. Keep credentials in user-managed environment variables or a host secret store; never put them in prompts or shared workflow files.

Use `list_operations()` for all REST and MCP input schemas; MCP operation names begin with `mcp_`. For example, `client.execute("mcp_seasonality", {"instrument": "eur_usd"})` calls the hosted seasonality tool when the account has access. `client.discover_mcp_tools()` refreshes the hosted catalogue.

`stream_events` is a finite capture bounded by `max_events` and `max_seconds`. It retains SSE IDs, event types and original data. No events does not imply there were no economic releases. The records projection is for presentation; use `.payload` whenever the complete response metadata or original nesting is required.

Forecast sources, units, dates and publication timestamps are preserved. FX reference rates are not executable broker quotes. The client never infers missing values or future release dates.

For historical research, use `history_path` with `revisions="all"`,
`series_mode="raw"` and `value_mode="source"`. Every page must explicitly confirm
source values and no normalization; unsupported responses raise an error.
`align_macro` selects only independently evidenced vintages before each decision,
retains the original reference period and excludes unknown publication timing.
Always inspect source units before combining different indicators or countries.
