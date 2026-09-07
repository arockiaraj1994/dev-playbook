"""
server.py - Dev Playbook MCP Server (SSE only; read-only rules + usage metrics).

Tools (3, all read-only, playbook_ namespaced):
 - playbook_start - THE entry point. mode=code (default): identity + guardrails +
                    matched workflow + next calls (+ optional ref="req:ST-101").
                    mode=prd|story: the authoring bootstrap.
 - playbook_get   - fetch one doc by ref= ("guardrails", "pattern:repository",
                    "language:kotlin/testing", "workflow:bug-fix") - the same
                    grammar the corpus uses in see_also:.
 - playbook_find  - list docs (no query) or search (with query); type= filter.

Run:
  uv run server.py

Transport: HTTP + Server-Sent Events on MCP_PORT (default 3000).

Auth model:
  MCP paths  (/sse, /messages/) - Bearer token, validated by identity.py
  Dashboard  (/dashboard/*, /login) - HttpOnly cookie session, validated by session.py
  Public     (/auth/login, /healthz, /login GET/POST, /logout)

Config (optional): config.toml next to server.py, or path in MCP_CONFIG.
  [enable] auth - default false.
  [admin] username / password - default admin (seeded on first run).
  Refuses to start with default admin/admin when MCP_HOST=0.0.0.0.

Other env vars:
  MCP_PORT - HTTP port (default 3000)
  MCP_HOST - bind host (default 127.0.0.1; 0.0.0.0 for LAN)
  MCP_DB_PATH - sqlite DB (default <repo>/mcp/data/metrics.db)
  MCP_INACTIVE_DAYS - "inactive" threshold (default 2)
  MCP_SNIPPET_SIZE - search snippet size chars (default 300, 50 - 5000)
  MCP_ADMIN_USER - override default admin username (default: admin)
  MCP_ADMIN_PASSWORD - override default admin password (default: admin)
  MCP_STANDARDS_ROOT - standards corpus root (default <repo>/standards)

Docs load from standards/ at boot; POST /dashboard/reload picks up edits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from mcp.types import (
    ServerCapabilities,
    TextContent,
    Tool,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from auth import AuthStore
from cache import CorpusCache
from corpus import standards_spec
from dashboard.auth_routes import build_auth_routes
from dashboard.routes import build_dashboard_routes
from identity import (
    EditorInfo,
    Principal,
    editor_var,
    principal_var,
    resolve_bearer_token,
    scope_principal,
)
from loader import DocStore, bootstrap_all, resolve_rules_root
from metrics import MetricsStore, summarize_args
from search import RulesSearchEngine
from session import DashboardSession
from tools import READ_ONLY
from tools import find as _find_mod
from tools import get as _get_mod
from tools import start as _start_mod

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dev-playbook")

SERVER_LABEL = os.getenv("MCP_SERVER_LABEL", "dev-playbook")
SERVER_VERSION = "0.9.0"
DEFAULT_PORT = 3000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_INACTIVE_DAYS = 2
_DEFAULT_DB_REL = Path("data") / "metrics.db"

# ---------------------------------------------------------------------------
# Startup - load the corpus + index
# ---------------------------------------------------------------------------

logger.info("Bootstrapping standards store...")
try:
    store: DocStore = bootstrap_all()
except FileNotFoundError as e:
    logger.error("%s", e)
    sys.exit(1)

if not store.all_docs():
    root = resolve_rules_root()
    found_subdirs = (
        sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        if root.is_dir()
        else []
    )
    hint = (
        f"Found subdirs but none had loadable .md rules: {found_subdirs}."
        if found_subdirs
        else "No project subdirectories found under standards/."
    )
    logger.error("No standards markdown docs loaded under %s. %s", root, hint)
    sys.exit(1)

standards_cache = CorpusCache(standards_spec())
# Docs are already in `store` from bootstrap_all; hand the same list to the
# cache rather than parsing the tree twice.
standards_cache._docs = store.all_docs()  # noqa: SLF001


def _on_standards_reload(_name: str, fresh: list) -> None:
    store.replace_all(fresh)
    engine.rebuild(store)


standards_cache._on_reload = _on_standards_reload  # noqa: SLF001

engine: RulesSearchEngine = RulesSearchEngine(store)
logger.info("Ready. Projects: %s", store.projects())

# Set by build_app(). The MCP `Server` is module-scoped, so dispatch_tool /
# _record_call (also module-scoped) read this through the module global rather
# than a closure.
metrics_store: MetricsStore | None = None

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server(SERVER_LABEL)

_TOOL_MODULES = [
    _start_mod,
    _get_mod,
    _find_mod,
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    # Every tool here is read-only; stamp the annotation centrally so a new
    # tool cannot ship without it. model_copy keeps module DEFINITIONS pristine.
    defs: list[Tool] = []
    for mod in _TOOL_MODULES:
        defs.extend(
            t if t.annotations else t.model_copy(update={"annotations": READ_ONLY})
            for t in mod.DEFINITIONS
        )
    return defs


@dataclass
class _CallContext:
    status: str = "ok"
    query: str | None = None
    doc_path: str | None = None
    top_result_path: str | None = None
    top_result_score: float | None = None


async def _dispatch_typed(name: str, arguments: dict, ctx: _CallContext) -> list[TextContent]:
    for mod in _TOOL_MODULES:
        result = await mod.dispatch(name, arguments, ctx, store, engine)
        if result is not None:
            return result
    ctx.status = "error"
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def dispatch_tool(name: str, arguments: dict) -> list[TextContent]:
    args_summary = summarize_args(arguments)
    logger.info("tool=%s args=%s", name, args_summary)
    started = time.monotonic()
    ctx = _CallContext()
    try:
        content = await _dispatch_typed(name, arguments, ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool dispatch failed: %s", exc)
        ctx.status = "error"
        latency_ms = int((time.monotonic() - started) * 1000)
        await _record_call(name, args_summary, latency_ms, ctx)
        raise
    latency_ms = int((time.monotonic() - started) * 1000)
    await _record_call(name, args_summary, latency_ms, ctx)
    return content


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await dispatch_tool(name, arguments)


async def _record_call(name: str, args_summary: str, latency_ms: int, ctx: _CallContext) -> None:
    if metrics_store is None:
        return
    principal = principal_var.get()
    editor = editor_var.get()
    if principal is None:
        return
    try:
        await metrics_store.record_call(
            user_id=principal.user_id,
            user_name=principal.user_name,
            editor_name=editor.name,
            tool_name=name,
            args_summary=args_summary,
            latency_ms=latency_ms,
            status=ctx.status,
            query=ctx.query,
            doc_path=ctx.doc_path,
            top_result_path=ctx.top_result_path,
            top_result_score=ctx.top_result_score,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record call metrics")


# ---------------------------------------------------------------------------
# Editor detection
# ---------------------------------------------------------------------------

_EDITOR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("claude-code", re.compile(r"claude[-\s]?code", re.IGNORECASE)),
    ("cursor", re.compile(r"cursor", re.IGNORECASE)),
    ("windsurf", re.compile(r"windsurf", re.IGNORECASE)),
    ("zed", re.compile(r"zed", re.IGNORECASE)),
    ("vscode", re.compile(r"vs ?code", re.IGNORECASE)),
]
_VERSION_RE = re.compile(r"(\d+(?:\.\d+){1,3})")


def editor_from_user_agent(ua: str | None) -> EditorInfo:
    if not ua:
        return EditorInfo(name="unknown", version="")
    for name, pat in _EDITOR_PATTERNS:
        if pat.search(ua):
            v_match = _VERSION_RE.search(ua)
            return EditorInfo(name=name, version=v_match.group(1) if v_match else "")
    first_token = ua.split("/", 1)[0].split()[0] if ua.split("/", 1)[0] else ua
    return EditorInfo(name=first_token.lower() or "unknown", version="")


def _editor_from_scope(scope: Scope) -> EditorInfo:
    headers = scope.get("headers") or []
    custom = next((v.decode("latin-1") for k, v in headers if k.lower() == b"x-mcp-editor"), None)
    if custom:
        parts = custom.split("/", 1)
        return EditorInfo(
            name=parts[0].strip().lower() or "unknown",
            version=parts[1].strip() if len(parts) > 1 else "",
        )
    ua = next((v.decode("latin-1") for k, v in headers if k.lower() == b"user-agent"), None)
    return editor_from_user_agent(ua)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpConfig:
    auth_enabled: bool = False
    admin_username: str = "admin"
    admin_password: str = "admin"


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.toml"


def load_mcp_config() -> McpConfig:
    raw = os.environ.get("MCP_CONFIG", "").strip()
    path = Path(raw).expanduser().resolve() if raw else _default_config_path()
    if not path.is_file():
        return McpConfig()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        logger.error("Could not read MCP config %s: %s", path, exc)
        sys.exit(1)
    except tomllib.TOMLDecodeError as exc:
        logger.error("Invalid TOML in MCP config %s: %s", path, exc)
        sys.exit(1)

    enable = data.get("enable") or {}
    if not isinstance(enable, dict):
        logger.error("MCP config %s: [enable] must be a table", path)
        sys.exit(1)

    admin_section = data.get("admin") or {}
    admin_username = os.environ.get("MCP_ADMIN_USER", "").strip() or str(
        admin_section.get("username", "admin")
    )
    admin_password = os.environ.get("MCP_ADMIN_PASSWORD", "").strip() or str(
        admin_section.get("password", "admin")
    )
    return McpConfig(
        auth_enabled=bool(enable.get("auth", False)),
        admin_username=admin_username,
        admin_password=admin_password,
    )


SERVER_INSTRUCTIONS = (
    "Development-standards playbook server. For any coding task, call "
    "playbook_start first - it returns guardrails, the matched workflow, and "
    'exact next calls. To author a PRD or story, call it with mode="prd" or '
    'mode="story". Fetch specific docs with playbook_get(ref=...), where a '
    "ref is the same string the corpus uses in see_also: frontmatter "
    '("guardrails", "pattern:repository", "req:ST-101"); discover them '
    "with playbook_find. Always pass the basename of the user's workspace "
    "directory as `project`."
)


def _initialization_options() -> InitializationOptions:
    return InitializationOptions(
        server_name=SERVER_LABEL,
        server_version=SERVER_VERSION,
        capabilities=ServerCapabilities(tools={}),
        instructions=SERVER_INSTRUCTIONS,
    )


# ---------------------------------------------------------------------------
# AppAuthMiddleware - single path-aware middleware
#
# MCP paths  (/sse, /messages/)    → identity.resolve_bearer_token (Bearer)
# Dashboard  (/dashboard/*, /login, /logout) → session.resolve_cookie (Cookie)
# Public     (/auth/login, /auth/token, /healthz) → anonymous principal
# ---------------------------------------------------------------------------

_MCP_PATH_PREFIXES = ("/sse", "/messages/")
_DASHBOARD_PATH_PREFIXES = ("/dashboard",)
# Login/logout are exact paths - `/dashboard` is the only prefix-matched route.
_LOGIN_LOGOUT_PATHS = frozenset({"/login", "/logout"})
# Exact public paths that never require any credential
_PUBLIC_PATHS = frozenset(
    [
        "/",
        "/auth/login",
        "/auth/token",
        "/healthz",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ]
)
# Static assets (css/js) are public - the login page needs them pre-auth.
_PUBLIC_PATH_PREFIXES = ("/dashboard/static/",)


def _is_dashboard_path(path: str) -> bool:
    return path in _LOGIN_LOGOUT_PATHS or any(path.startswith(p) for p in _DASHBOARD_PATH_PREFIXES)


def _inject_cookie_send(send: Send, cookie_header: tuple[bytes, bytes]) -> Send:
    """Wrap send to inject a Set-Cookie header into the first http.response.start message."""
    injected = [False]

    async def wrapped(message: dict) -> None:
        if not injected[0] and message.get("type") == "http.response.start":
            injected[0] = True
            headers = list(message.get("headers", [])) + [cookie_header]
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


class AppAuthMiddleware:
    """
       Path-aware authentication middleware.

    - MCP paths use Bearer token auth (identity.py).
    - Dashboard paths use HttpOnly cookie session auth (session.py).
    - Public paths pass through without any credential check.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        auth_enabled: bool,
        auth_store: AuthStore,
        dashboard_session: DashboardSession,
    ) -> None:
        self._app = app
        self._auth_enabled = auth_enabled
        self._auth_store = auth_store
        self._session = dashboard_session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        principal = await self._resolve(scope, send)
        if principal is None:
            return  # response already sent (401 or redirect)

        state = scope.setdefault("state", {})
        state["principal"] = principal
        token = principal_var.set(principal)

        path = scope.get("path", "")
        headers = dict(scope.get("headers") or [])
        secure = headers.get(b"x-forwarded-proto", b"").decode() == "https"

        # Sliding idle timeout: re-issue session cookie on every authenticated dashboard response
        refresh_token = state.get("_session_token")
        if refresh_token:
            send = _inject_cookie_send(
                send, self._session.refresh_cookie_header(refresh_token, secure=secure)
            )

        # CSRF cookie: set on dashboard GET requests when not already present
        is_dashboard_get = _is_dashboard_path(path) and scope.get("method", "GET") == "GET"
        if is_dashboard_get and not self._session.read_csrf_cookie(scope):
            from session import DashboardSession as _DS

            csrf_token = _DS.generate_csrf_token()
            send = _inject_cookie_send(
                send, self._session.csrf_cookie_header(csrf_token, secure=secure)
            )
            # Stash so _csrf_token() in routes reads the same value from the cookie
            scope.setdefault("state", {})["_fresh_csrf"] = csrf_token

        try:
            await self._app(scope, receive, send)
        finally:
            principal_var.reset(token)

    async def _resolve(self, scope: Scope, send: Send) -> Principal | None:
        path = scope.get("path", "")

        # Public paths: no auth required
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PATH_PREFIXES):
            return Principal(user_id="public", user_name="public", role="user")

        # MCP paths: Bearer token only
        if any(path.startswith(p) for p in _MCP_PATH_PREFIXES):
            return await resolve_bearer_token(scope, send, self._auth_store, self._auth_enabled)

        # Dashboard + login/logout: cookie session
        if _is_dashboard_path(path):
            return await self._resolve_dashboard(scope, send)

        # Everything else: anonymous (should not normally be reached)
        return Principal(user_id="public", user_name="public", role="user")

    async def _resolve_dashboard(self, scope: Scope, send: Send) -> Principal | None:
        path = scope.get("path", "")

        # /login and /logout are always reachable without a session
        if path == "/login" or path == "/logout":
            return Principal(user_id="public", user_name="public", role="user")

        if not self._auth_enabled:
            from identity import _anonymous_principal

            return _anonymous_principal(scope)

        principal = await self._session.resolve_cookie(scope)
        if principal is not None:
            # Store raw token so __call__ can slide the idle timeout on every response
            from session import _read_cookie as _rc

            raw_token = _rc(scope, "session")
            if raw_token:
                scope.setdefault("state", {})["_session_token"] = raw_token
            return principal

        # No valid session - redirect to /login preserving the original path
        location = f"/login?next={path}"
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", location.encode()),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})
        return None


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


@dataclass
class AppDeps:
    cfg: McpConfig
    metrics: MetricsStore
    inactive_days: int
    auth_store: AuthStore
    sse_transport: SseServerTransport | None = field(default=None)


def build_app(deps: AppDeps) -> Starlette:
    global metrics_store
    metrics_store = deps.metrics

    sse = deps.sse_transport or SseServerTransport("/messages/")
    deps.sse_transport = sse

    dashboard_session = DashboardSession(deps.auth_store)

    # -- SSE / MCP -----------------------------------------------------------

    async def mcp_sse_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        principal: Principal | None = scope_principal(scope)
        editor = _editor_from_scope(scope)
        p_token = principal_var.set(principal)
        e_token = editor_var.set(editor)
        try:
            if metrics_store is not None and principal is not None:
                try:
                    await metrics_store.upsert_registration(
                        user_id=principal.user_id,
                        user_name=principal.user_name,
                        editor_name=editor.name,
                        editor_version=editor.version,
                    )
                except Exception:
                    logger.exception("upsert_registration failed")
            async with sse.connect_sse(scope, receive, send) as streams:
                await server.run(streams[0], streams[1], _initialization_options())
        finally:
            principal_var.reset(p_token)
            editor_var.reset(e_token)

    async def sse_endpoint(request: Request) -> Response:
        await mcp_sse_asgi(request.scope, request.receive, request._send)  # noqa: SLF001
        return Response()

    # -- JSON auth endpoints (MCP client token issuance) --------------------

    async def json_login_endpoint(request: Request) -> Response:
        """POST /auth/login - JSON, public. Returns an MCP Bearer token."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", "")).strip()
        if not username or not password:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        user = await deps.auth_store.verify_login(username, password)
        if user is None:
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
        raw_days = body.get("expires_in_days")
        expires_in_days: int | None = None
        if raw_days is not None:
            try:
                expires_in_days = int(raw_days)
                if expires_in_days < 1:
                    expires_in_days = None
            except (ValueError, TypeError):
                pass
        token_data = await deps.auth_store.create_token(
            user["id"], expires_in_days, token_type="mcp"
        )
        return JSONResponse(
            {
                "token": token_data["token"],
                "role": user["role"],
                "expires_at": token_data["expires_at"],
            }
        )

    async def generate_mcp_token_endpoint(request: Request) -> Response:
        """POST /auth/token - JSON, requires valid MCP Bearer token."""
        principal = scope_principal(request.scope)
        if principal is None or principal.user_id == "public":
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        raw_days = body.get("expires_in_days")
        expires_in_days: int | None = None
        if raw_days is not None:
            try:
                expires_in_days = int(raw_days)
                if expires_in_days < 1:
                    expires_in_days = None
            except (ValueError, TypeError):
                return JSONResponse({"error": "invalid expires_in_days"}, status_code=400)
        token_data = await deps.auth_store.create_token(
            principal.user_id, expires_in_days, token_type="mcp"
        )
        return JSONResponse(token_data)

    # -- Browser login / logout routes ---------------------------------------

    login_get, login_post, logout_post = build_auth_routes(
        deps.auth_store, dashboard_session, SERVER_LABEL
    )

    # -- OAuth 2.0 discovery endpoints (RFC 8414 / RFC 8707) -------------------
    # The MCP SDK pre-flights these on every SSE connection attempt.

    async def oauth_protected_resource(request: Request) -> Response:
        """GET /.well-known/oauth-protected-resource - RFC 8707."""
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "resource": base,
                "authorization_servers": [base],
                "bearer_methods_supported": ["header"],
            }
        )

    async def oauth_authorization_server(request: Request) -> Response:
        """GET /.well-known/oauth-authorization-server - RFC 8414."""
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": base,
                "token_endpoint": f"{base}/auth/login",
                "token_endpoint_auth_methods_supported": ["none"],
                "grant_types_supported": ["urn:ietf:params:oauth:grant-type:device_code"],
                "response_types_supported": ["token"],
                "scopes_supported": ["mcp"],
            }
        )

    routes = [
        # OAuth discovery (must be public, before auth middleware intercepts)
        Route(
            "/.well-known/oauth-protected-resource",
            endpoint=oauth_protected_resource,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=oauth_authorization_server,
            methods=["GET"],
        ),
        # Public JSON API for MCP clients
        Route("/auth/login", endpoint=json_login_endpoint, methods=["POST"]),
        Route("/auth/token", endpoint=generate_mcp_token_endpoint, methods=["POST"]),
        # Browser login / logout
        Route("/login", endpoint=login_get, methods=["GET"]),
        Route("/login", endpoint=login_post, methods=["POST"]),
        Route("/logout", endpoint=logout_post, methods=["POST"]),
        # MCP SSE transport
        Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        # Dashboard UI
        Mount(
            "/dashboard",
            routes=build_dashboard_routes(
                deps.metrics,
                deps.inactive_days,
                SERVER_LABEL,
                deps.auth_store,
                dashboard_session,
                auth_enabled=deps.cfg.auth_enabled,
                rules_store=store,
                rules_root=resolve_rules_root(),
                standards_cache=standards_cache,
            ),
        ),
        Route("/", endpoint=_root_redirect, methods=["GET"]),
        Route("/healthz", endpoint=_healthz, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(
        AppAuthMiddleware,
        auth_enabled=deps.cfg.auth_enabled,
        auth_store=deps.auth_store,
        dashboard_session=dashboard_session,
    )
    return app


async def _root_redirect(_request: Request) -> Response:
    from starlette.responses import RedirectResponse

    return RedirectResponse("/dashboard/", status_code=302)


async def _healthz(_request: Request) -> Response:
    return Response("ok", media_type="text/plain")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path:
    raw = os.environ.get("MCP_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent / _DEFAULT_DB_REL


def _resolve_inactive_days() -> int:
    raw = os.environ.get("MCP_INACTIVE_DAYS", "").strip()
    if not raw:
        return DEFAULT_INACTIVE_DAYS
    try:
        v = int(raw)
        return max(v, 1)
    except ValueError:
        return DEFAULT_INACTIVE_DAYS


async def _serve() -> None:
    cfg = load_mcp_config()
    port = _int_env("MCP_PORT", DEFAULT_PORT)
    host = os.environ.get("MCP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST

    # Refuse default admin/admin when binding to all interfaces.
    if (
        host in ("0.0.0.0", "::")
        and cfg.admin_username == "admin"
        and cfg.admin_password == "admin"
    ):
        logger.error(
            "Refusing to start: MCP_HOST=%s with default admin/admin credentials. "
            "Set MCP_ADMIN_PASSWORD (and preferably MCP_ADMIN_USER) to a strong "
            "value, or bind to 127.0.0.1.",
            host,
        )
        sys.exit(1)

    db_path = _resolve_db_path()
    metrics = MetricsStore(db_path)
    await metrics.init()

    auth_store = AuthStore(db_path)
    await auth_store.init()
    await auth_store.seed_default_admin(cfg.admin_username, cfg.admin_password)
    logger.info("Auth store ready (default admin: %s)", cfg.admin_username)

    days = _resolve_inactive_days()
    app = build_app(AppDeps(cfg=cfg, metrics=metrics, inactive_days=days, auth_store=auth_store))

    logger.info(
        "Starting on http://%s:%d (auth=%s, inactive_days=%d, db=%s)",
        host,
        port,
        cfg.auth_enabled,
        days,
        db_path,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.error("%s must be an integer, got %r", name, raw)
        sys.exit(1)


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
