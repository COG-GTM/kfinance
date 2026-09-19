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
| `CLIENT_TOKENS` | Yes** | — | Comma separated bearer tokens that inbound MCP clients must present |
| `CLIENT_AUTH_DISABLED` | No | `false` | Set to `true` only when an external gateway authenticates inbound clients |
| `CLIENT_CORS_ORIGINS` | No | — | Comma separated browser origins allowed to call the proxy cross-origin |

*Either both `AUTH_CLIENT_ID` and `AUTH_PRIVATE_KEY`, or `AUTH_REFRESH_TOKEN` must be set.

**Either `CLIENT_TOKENS` or `CLIENT_AUTH_DISABLED=true` must be set; the server refuses to start otherwise.

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
export CLIENT_TOKENS="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m kfinance.proxy_mcp --host 127.0.0.1 --port 8000
```

The server starts on `http://127.0.0.1:8000/mcp` using streamable-http transport.

Once the server is running, you can test it with the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

```bash
npx @modelcontextprotocol/inspector
```

In the inspector, connect using URL `http://127.0.0.1:8000/mcp` with transport type "Streamable HTTP" and an `Authorization: Bearer <one of CLIENT_TOKENS>` header.

| CLI Option | Default | Description |
|-----------|---------|-------------|
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to bind to |

The default bind is loopback only. Binding to a non-loopback interface (for example
`--host 0.0.0.0` in a container) publishes the proxy on the network and must be paired with
client tokens or a fronting authenticating gateway.

## Client Authentication

The proxy injects the operator's credential into every forwarded request, so inbound clients
must authenticate. Requests to `/mcp` are rejected with `401` unless they carry
`Authorization: Bearer <token>` matching one of the `CLIENT_TOKENS` values (`GET /health`
is exempt). The server refuses to start when no tokens are configured.

Set `CLIENT_AUTH_DISABLED=true` only when the proxy runs behind a gateway, VPN, or service
mesh that authenticates callers itself; in that mode any caller that reaches the proxy can
spend the operator's subscription.

Pre-shared tokens are the simplest option. Richer alternatives for a production deployment:

- **OAuth 2.0 Proxy** — The proxy runs its own OAuth flow (e.g., via FastMCP's built-in `OAuthProxy`). Clients register, get redirected to an IdP like Okta, and receive scoped tokens. 
- **JWT Validation** — Clients bring their own IdP-issued tokens. The proxy validates them against the IdP's JWKS endpoint (FastMCP provides `JWTVerifier` for this). Simpler than a full OAuth flow but requires clients to obtain tokens independently.
- **Network-Level Trust** — No application-layer auth. The proxy is deployed behind a VPN, service mesh (e.g., Istio with mTLS), or internal load balancer so that only trusted services can reach it.

## CORS

Browsers can only call the proxy from the origins listed in `CLIENT_CORS_ORIGINS`, which is
empty by default (no cross-origin access). Credentialed cross-origin requests are not
allowed, so browser clients must send the client token in the `Authorization` header.

## Production Considerations

Beyond client authentication, a production deployment would additionally need:

- A more comprehensive health check (the current `GET /health` stub does not verify backend connectivity or token validity)
- Sentry or equivalent error tracking
- Redis for shared OAuth client state across replicas (if using OAuth proxy)
- Kubernetes deployment manifests and service configuration
