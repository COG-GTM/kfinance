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
| `INBOUND_TOKEN` | Yes | — | Pre-shared token that inbound MCP clients must send as `Authorization: Bearer <token>` |
| `INBOUND_ALLOWED_ORIGINS` | No | `[]` | JSON list of browser origins allowed to call the proxy cross-origin, e.g. `["https://app.example.com"]`. Empty disables CORS entirely. |

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

```bash
export INBOUND_TOKEN="a-long-random-secret"
python -m kfinance.proxy_mcp --host 127.0.0.1 --port 8000
```

The server starts on `http://127.0.0.1:8000/mcp` using streamable-http transport.

Once the server is running, you can test it with the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
npx @modelcontextprotocol/inspector
```

In the inspector, connect using URL `http://127.0.0.1:8000/mcp` with transport type "Streamable HTTP" and set the `Authorization` header to `Bearer <INBOUND_TOKEN>`.

The proxy defaults to binding loopback. Any non-loopback bind (e.g. `--host 0.0.0.0` in a container) must sit behind an authenticating gateway in addition to the pre-shared token.

| CLI Option | Default | Description |
|-----------|---------|-------------|
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to bind to |

## Client Authentication

The proxy injects the operator's backend credential into every forwarded request, so inbound
callers are authenticated before anything is forwarded. This skeleton requires a pre-shared token:
set `INBOUND_TOKEN` and have clients send `Authorization: Bearer <INBOUND_TOKEN>`. Requests without
it get a `401`; only `GET /health` is unauthenticated. The server refuses to start if
`INBOUND_TOKEN` is unset.

Browser access is closed by default: CORS is only enabled when `INBOUND_ALLOWED_ORIGINS` lists
explicit origins, and wildcard origins are never used with credentials.

For a production deployment, consider replacing the pre-shared token with one of the following:

- **OAuth 2.0 Proxy** — The proxy runs its own OAuth flow (e.g., via FastMCP's built-in `OAuthProxy`). Clients register, get redirected to an IdP like Okta, and receive scoped tokens. 
- **JWT Validation** — Clients bring their own IdP-issued tokens. The proxy validates them against the IdP's JWKS endpoint (FastMCP provides `JWTVerifier` for this). Simpler than a full OAuth flow but requires clients to obtain tokens independently.
- **API Key / Static Token** — What this skeleton implements: the proxy checks for a pre-shared secret in request headers. Simple and appropriate for internal services or controlled partner integrations.
- **Network-Level Trust** — No application-layer auth. The proxy is deployed behind a VPN, service mesh (e.g., Istio with mTLS), or internal load balancer so that only trusted services can reach it.

## Production Considerations

Beyond client authentication, a production deployment would additionally need:

- A more comprehensive health check (the current `GET /health` stub does not verify backend connectivity or token validity)
- Sentry or equivalent error tracking
- Redis for shared OAuth client state across replicas (if using OAuth proxy)
- Kubernetes deployment manifests and service configuration
