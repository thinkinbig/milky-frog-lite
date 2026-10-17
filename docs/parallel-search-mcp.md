# Web research with Parallel Search MCP

Use [Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
to find current API documentation and read source pages during a coding Run.
It offers free web search and page fetching without a Parallel account or API
key. Anonymous search uses Fast mode and is intended for exploration and light
use; usage limits apply.

## Setup

First complete the [Milky Frog setup](../README.md#setup), including your model
configuration. You also need Node.js 22+ and `npx` on your host's `PATH`.
Milky Frog's MCP client uses stdio, so
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) bridges it to Parallel's
HTTP endpoint. The bridge runs on the host even when you use the Container Sandbox.

Download the bridge before starting Milky Frog so its first installation does not
consume the MCP client's 30-second connection timeout:

```bash
npm exec --yes --package=mcp-remote@0.15.1 -- node --version
```

In your Workspace, create `.milky-frog/mcp.json` with this configuration. If it
already exists, add only the `parallel` entry to its `mcpServers` object, keeping
your other entries:

```json
{
  "mcpServers": {
    "parallel": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@0.15.1",
        "https://search.parallel.ai/mcp",
        "--transport",
        "http-only",
        "--header",
        "User-Agent: milky-frog/0.1.0 (https://github.com/thinkinbig/milky-frog-lite)"
      ],
      "enabled": true
    }
  }
}
```

For all Workspaces, use `~/.milky-frog/mcp.json` instead (or `mcp.json` under
`MILKY_FROG_HOME` if set). A Workspace entry overrides a user-level entry with
the same name. No Parallel credentials or additional Python dependencies are needed.

## Use it in a Run

From the configured Workspace, check that `parallel` is listed as enabled:

```bash
uv run milky-frog mcp list
```

This lists configuration; it does not test a connection. Start a new Milky Frog
process to connect the server and expose `parallel__web_search` and
`parallel__web_fetch` as Tools:

```bash
uv run milky-frog run "Use parallel__web_search to find the current Python asyncio TaskGroup documentation, then use parallel__web_fetch to read the official page. Summarize its cancellation behavior and include source URLs."
```

This adds optional Tools alongside the built-in Tools. It does not replace
`fetch`, enable Jina's `web_search`, or change your model configuration. Search
queries and fetched URLs are sent to Parallel, so keep private source code and
credentials out of research requests.

To stop loading the server, disable it from the same Workspace and restart
Milky Frog:

```bash
uv run milky-frog mcp disable parallel
```

Use `uv run milky-frog mcp enable parallel` to enable it again.

## Troubleshooting

- If startup reports a failed MCP connection, check that `node` and `npx` are
  available in the terminal launching Milky Frog, run the download command above,
  and restart it. No Tools are added when the server cannot connect.
- If `parallel` is missing or has an unexpected state, check both configuration
  locations for malformed JSON or a same-named Workspace override.
- If Parallel returns a usage-limit error, wait before trying again. For higher
  limits, see the [Parallel setup options](https://docs.parallel.ai/integrations/mcp/search-mcp#installation).
