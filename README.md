# kFinance

The kFinance Python library provides a simple interface for the LLM-ready API, streamlining API requests and response handling. It can be used on its own, with LLMs, or integrated into applications.

For a complete overview of the functions, usage, and features of the kFinance Python library, please refer to documentation [here](https://kensho-kfinance.readthedocs.io/en/stable/).

Any questions or suggestions can be sent to the [kFinance Maintainers](kfinance-maintainers@kensho.com).

# Setup

You can install kFinance on [PyPI](https://pypi.org/project/kensho-kfinance/) via

`pip install kensho-kfinance`

# Getting started

To receive access, please email [S&P Global Market Intelligence](market.intelligence@spglobal.com) for information on free trials and pricing.

Once access is obtained, get started using the [Authentication Guide](https://docs.kensho.com/llmreadyapi/kf-authentication) and [Usage Guide](https://docs.kensho.com/llmreadyapi/usage).

To get started, we provide some notebooks:

- The [LLM-ready API Basic Usage](example_notebooks%2Fbasic_usage.ipynb) notebook demonstrates how
  fetch data with the kFinance client.
- The [tool_calling notebooks](example_notebooks%2Ftool_calling) show how the kFinance library can
  be used for tool calling. We provide notebooks for OpenAI (GPT), Anthropic (Claude), and Google
  (Gemini). Each of these integrations comes in a langchain version, which uses langchain as a
  wrapper to simplify the integration, and as a lower level non-langchain version.

We also provide an [interactive notebook](example_notebooks/basic_usage.ipynb) that demonstrates some usage examples.

# MCP (Model Context Protocol)

To run the kFinance MCP server use:

`python -m kfinance.mcp`

This function initializes and starts an MCP server that exposes the kFinance tools. The server supports multiple authentication methods and transport protocols to accommodate different deployment scenarios.

The server's full signature is as follows:

`kfinance.mcp [--stdio|-s|--sse|--streamable-http] [--private-key-file <path>]`

Credentials are read from environment variables (or, for the private key, from a file) so that
they never appear on the command line, where any user on the same host could read them from the
process list (`ps`, `/proc/<pid>/cmdline`) and where they would be kept in shell history.

| Environment variable        | Purpose                                                    |
|-----------------------------|------------------------------------------------------------|
| `KFINANCE_REFRESH_TOKEN`    | OAuth refresh token                                        |
| `KFINANCE_CLIENT_ID`        | Client id for key-pair authentication                      |
| `KFINANCE_PRIVATE_KEY`      | Private key for key-pair authentication                    |
| `KFINANCE_PRIVATE_KEY_FILE` | Path to a file containing the private key (same as `--private-key-file`) |

Authentication Methods (in order of precedence):

1. Refresh Token: Uses an existing refresh token for authentication. `KFINANCE_REFRESH_TOKEN` must be set.
2. Key Pair: Uses client ID and private key for authentication. `KFINANCE_CLIENT_ID` must be set, together with either `KFINANCE_PRIVATE_KEY` or a private key file (`--private-key-file <path>` / `KFINANCE_PRIVATE_KEY_FILE`). Restrict the key file to your user, e.g. `chmod 600 <path>`.
3. Browser: Falls back to browser-based authentication flow. This occurs if no credentials are provided.

The `--refresh-token`, `--client-id` and `--private-key` flags are still accepted for backwards
compatibility, but `--refresh-token` and `--private-key` are deprecated and print a warning,
because values passed this way are exposed in the process list.

Transport Layers:

- `--stdio` / `-s`: Standard input/output transport (use with MCP Inspector)
- `--sse`: Server-Sent Events transport (default)
- `--streamable-http`: HTTP transport

Examples:
```bash
# Read the token without echoing it or writing it to shell history
read -rs KFINANCE_REFRESH_TOKEN && export KFINANCE_REFRESH_TOKEN

# Using stdio with MCP Inspector: put the token in the `env` block of an MCP config file
# (see the JSON example below, `chmod 600` it) instead of passing `-e KEY=value`, which
# would expose the value in the inspector's own command line.
npx @modelcontextprotocol/inspector --config kfinance_mcp.json --server kfinance

# Using SSE (default)
python -m kfinance.mcp

# Using streamable-http with a key pair
KFINANCE_CLIENT_ID=<client-id> python -m kfinance.mcp --streamable-http --private-key-file ~/.kfinance/private_key.pem
```

MCP client configuration (e.g. Claude Desktop `claude_desktop_config.json`) should pass
credentials through `env` rather than `args`:

```json
{
  "mcpServers": {
    "kfinance": {
      "command": "python",
      "args": ["-m", "kfinance.mcp", "--stdio"],
      "env": {
        "KFINANCE_REFRESH_TOKEN": "<refresh-token>"
      }
    }
  }
}
```

## MCP Proxy

The kFinance MCP proxy is a skeleton server that forwards requests to a remote kfinance MCP backend, injecting authentication tokens into every outgoing request. It is intended as a starting point for building a full production MCP proxy service.

```bash
# Using a refresh token (for experimentation)
AUTH_REFRESH_TOKEN=<token> python -m kfinance.proxy_mcp

# Using a key pair (for production)
AUTH_CLIENT_ID=<client-id> AUTH_PRIVATE_KEY=<private-key> python -m kfinance.proxy_mcp
```

The server starts on `http://127.0.0.1:8000/mcp` by default. Use `--host` and `--port` to configure binding.

For full documentation including configuration options, authentication methods, and production considerations, see [kfinance/integrations/proxy_mcp/README.md](kfinance/integrations/proxy_mcp/README.md).

# Development

## Working with Local Package Version

If you need to develop using a local version of the kFinance package in another project that uses [poetry](https://python-poetry.org/) for package and dependency management, follow these steps:

1. In your dependent project's `pyproject.toml`, replace the kFinance package version specification:

   ```toml
   # Replace this:
   # kensho-kfinance = "~2.0.1"
   # With this:
   kensho-kfinance = { path = "/absolute/path/to/kfinance", develop = true }
   ```

   The `develop = true` flag ensures that the package always matches your local version without requiring reinstallation after changes.

2. Update your project's dependencies:

   ```bash
   poetry update    # Update poetry.lock with new changes
   poetry install   # Install dependencies with updated configuration
   ```

   If you encounter the error "your pyproject.toml file has significantly changed", run:

   ```bash
   poetry lock     # Sync the changes to poetry.lock
   ```

# Versioning

The kFinance uses semantic versioning (major, minor, patch).
To bump the version, add a new entry in [CHANGELOG.md](kfinance%2FCHANGELOG.md).
This will generate a new version of the library as part of the release process.

# License

Licensed under the Apache 2.0 License. Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

Copyright 2025-present Kensho Technologies, LLC. The present date is determined by the timestamp of the most recent commit in the repository.
