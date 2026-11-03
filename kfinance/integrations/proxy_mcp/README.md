# Proxy MCP

This is a skeleton MCP proxy server that forwards requests to Kensho's remote LLM-ready API MCP backend and injects authentication tokens into every outgoing request. It is intended as a starting point for building a full production service.

Under the hood, it uses FastMCP's [Proxy Provider](https://gofastmcp.com/servers/providers/proxy), which handles tool/resource/prompt discovery and forwarding between the local server and the remote backend.

## How It Works

The proxy sits between an MCP client and our MCP server at `https://kfinance.kensho.com/integrations/mcp`. It handles token management transparently — clients connect to the proxy without needing to manage OAuth themselves.

```
MCP Client  -->  Proxy (this service)  -->  kfinance.kensho.com/integrations/mcp
                 (injects Bearer token)
```

## Configuration

All configuration is via environment variables (powered by pydantic-settings).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BACKEND_URL` | No | `https://kfinance.kensho.com/integrations/mcp` | Remote MCP server URL |
| `AUTH_CLIENT_ID` | Yes* | — | Client ID for key pair authentication |
| `AUTH_PRIVATE_KEY` | Yes* | — | Private key for key pair authentication |
| `AUTH_OKTA_HOST` | No | `https://kensho.okta.com` | Okta host URL |
| `AUTH_REFRESH_TOKEN` | Yes* | — | Refresh token for obtaining access tokens (local dev fallback) |
| `AUTH_REFRESH_URL` | No | `https://kfinance.kensho.com/oauth2/refresh` | Token refresh endpoint |
| `INBOUND_TOKEN` | Yes | — | Pre-shared token MCP clients must send as `Authorization: Bearer <token>` |
| `INBOUND_ALLOWED_ORIGINS` | No | `[]` | JSON list of browser origins allowed to call the proxy, e.g. `["https://console.example.com"]`. `*` is rejected |

*Either both `AUTH_CLIENT_ID` and `AUTH_PRIVATE_KEY`, or `AUTH_REFRESH_TOKEN` must be set.

## Authentication Methods

### Option 1: Refresh Token (for initial experimentation)

The quickest way to get started. Obtain a refresh token from https://kfinance.kensho.com/manual_login/ and set it as an environment variable:

```bash
export AUTH_REFRESH_TOKEN="your-refresh-token"
```

Note that refresh tokens are short-lived and tied to a single user session. For production use cases, a key pair must be used instead.

### Option 2: Key Pair (required for production)

Follow the guide at https://docs.kensho.com/llmreadyapi/python-library/kf-authentication#publicprivate-key to obtain a client ID and private key, then:

```bash
export AUTH_CLIENT_ID="your-client-id"
export AUTH_PRIVATE_KEY="your-private-key"
```

## Running

Generate a token that MCP clients will use to authenticate to the proxy:

```bash
export INBOUND_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m kfinance.integrations.proxy_mcp.proxy_mcp --host 127.0.0.1 --port 8000
```

The server starts on `http://127.0.0.1:8000/mcp` using streamable-http transport. It refuses to start without `INBOUND_TOKEN`.

Once the server is running, you can test it with the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
npx @modelcontextprotocol/inspector
```

In the inspector, connect using URL `http://127.0.0.1:8000/mcp` with transport type "Streamable HTTP" and add the header `Authorization: Bearer $INBOUND_TOKEN`. If you use the inspector's browser UI to connect directly (not via its local proxy), add its origin to `INBOUND_ALLOWED_ORIGINS`.

| CLI Option | Default | Description |
|-----------|---------|-------------|
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to bind to |

## Client Authentication

The proxy uses the operator's kfinance credentials for every forwarded request, so it only serves authenticated callers:

- Every request except `GET /health` must carry `Authorization: Bearer $INBOUND_TOKEN`; otherwise the proxy returns `401`.
- Any request with an `Origin` header that is not listed in `INBOUND_ALLOWED_ORIGINS` gets a `403`, so web pages the operator visits (including DNS-rebinding attempts) cannot drive the proxy.
- CORS is off unless `INBOUND_ALLOWED_ORIGINS` is set. When it is set, only those origins get CORS headers, credentials (cookies) are never allowed, and only the MCP methods and headers are permitted.

The static token suits local use and controlled internal deployments. For multi-user production deployments, replace it with per-client identity, for example:

- **OAuth 2.0 Proxy** — The proxy runs its own OAuth flow (e.g., via FastMCP's built-in `OAuthProxy`). Clients register, get redirected to an IdP like Okta, and receive scoped tokens.
- **JWT Validation** — Clients bring their own IdP-issued tokens. The proxy validates them against the IdP's JWKS endpoint (FastMCP provides `JWTVerifier` for this).
- **Network-Level Trust** — In addition to the above, deploy behind a VPN, service mesh (e.g., Istio with mTLS), or internal load balancer so that only trusted services can reach it. Binding to a non-loopback `--host` logs a warning.

## Production Considerations

Beyond client authentication, a production deployment would additionally need:

- A more comprehensive health check (the current `GET /health` stub does not verify backend connectivity or token validity)
- Sentry or equivalent error tracking
- Redis for shared OAuth client state across replicas (if using OAuth proxy)
- Kubernetes deployment manifests and service configuration
