"""
dashboard/routes.py - Starlette routes for the metrics UI.

Sections:
  /dashboard/ - users + adoption overview
  /dashboard/tools - tool popularity, latency, rule docs fetched
  /dashboard/searches - search query log + zero-result queries
  /dashboard/activity - recent calls (last 100)
  /dashboard/users/{name} - per-user drill-down
  /dashboard/tokens - MCP token management (all authenticated users)
  /dashboard/users-admin - user management (admin only)
  /dashboard/static/... - CSS

Authentication is handled at the app level by AppAuthMiddleware. When
auth.enabled is true the middleware rejects / redirects unauthenticated
requests before they reach these handlers.

CSRF: All state-changing POST handlers validate the double-submit cookie.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from identity import scope_principal
from metrics import MetricsStore
from standards_store import VersionConflict

if TYPE_CHECKING:
    from auth import AuthStore
    from session import DashboardSession
    from standards_store import StandardsStore

_DASHBOARD_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _DASHBOARD_DIR / "templates"
_STATIC_DIR = _DASHBOARD_DIR / "static"


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["since"] = _since_filter
    templates.env.filters["short_dt"] = _short_dt_filter
    templates.env.filters["fm_value"] = _fm_value_filter
    return templates


def _fm_value_filter(value: object) -> str:
    """Render a frontmatter value for the two-column table (lists joined)."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


# ---------------------------------------------------------------------------
# Jinja filters
# ---------------------------------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _since_filter(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return " - "
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _short_dt_filter(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return " - "
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def build_dashboard_routes(
    store: MetricsStore,
    inactive_days: int,
    server_label: str = "dev-playbook",
    auth_store: AuthStore | None = None,
    dashboard_session: DashboardSession | None = None,
    auth_enabled: bool = False,
    standards: StandardsStore | None = None,
) -> list[BaseRoute]:
    templates = _build_templates()

    def _csrf_token(request: Request) -> str:
        """Read existing CSRF cookie or the middleware-generated fresh token."""
        if dashboard_session is not None:
            existing = dashboard_session.read_csrf_cookie(request.scope)
            if existing:
                return existing
        # Middleware may have generated a fresh token and stashed it in scope state
        fresh = (request.scope.get("state") or {}).get("_fresh_csrf")
        if fresh:
            return fresh
        from session import DashboardSession as _DS

        return _DS.generate_csrf_token()

    def _validate_csrf_or_403(request: Request, form: dict) -> Response | None:
        """Return a 403 Response if CSRF validation fails, else None."""
        if dashboard_session is None:
            return None
        cookie_val = dashboard_session.read_csrf_cookie(request.scope)
        form_val = str(form.get("_csrf", ""))
        if not dashboard_session.validate_csrf(cookie_val, form_val):
            return HTMLResponse("403 Forbidden - invalid CSRF token.", status_code=403)
        return None

    def _ctx(request: Request, **extra: object) -> dict:
        principal = scope_principal(request.scope)
        is_admin = principal is not None and getattr(principal, "role", "user") == "admin"
        csrf_token = _csrf_token(request)
        return {
            "request": request,
            "principal": principal,
            "is_admin": is_admin,
            "inactive_days": inactive_days,
            "server_label": server_label,
            "csrf_token": csrf_token,
            **extra,
        }

    def _forbidden(request: Request) -> Response:
        return HTMLResponse("<p>403 Forbidden - admin access required.</p>", status_code=403)

    # -- Read-only views -----------------------------------------------------

    _WINDOWS = {"24h": 1, "7d": 7, "30d": 30}

    def _window_days(request: Request) -> tuple[str, int]:
        window = str(request.query_params.get("window", "24h"))
        if window not in _WINDOWS:
            window = "24h"
        return window, _WINDOWS[window]

    async def dashboard_view(request: Request) -> Response:
        import json as _json

        window, window_days = _window_days(request)
        summary = await store.dashboard_summary(
            inactive_days=inactive_days, window_days=window_days
        )
        calls = await store.list_recent_calls(limit=8)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            _ctx(
                request,
                summary=summary,
                calls=calls,
                window=window,
                hourly_json=_json.dumps(summary.hourly),
                daily_json=_json.dumps(summary.daily),
                page="dashboard",
            ),
        )

    async def summary_api(request: Request) -> Response:
        """Live-dashboard poll: KPIs, chart series, live users, recent calls."""
        window, window_days = _window_days(request)
        summary = await store.dashboard_summary(
            inactive_days=inactive_days, window_days=window_days
        )
        calls = await store.list_recent_calls(limit=8)
        return JSONResponse(
            {
                "window": window,
                "connected": summary.connected,
                "calls_today": summary.calls_today,
                "calls_trend": summary.calls_trend,
                "zero_today": summary.zero_today,
                "zero_trend": summary.zero_trend,
                "configured": summary.configured,
                "active": summary.active,
                "inactive": summary.inactive,
                "never_called": summary.never_called,
                "hourly": summary.hourly,
                "daily": summary.daily,
                "live_users": summary.live_users,
                "top_tools": summary.top_tools,
                "recent_calls": [
                    {
                        "created_at": c.created_at,
                        "user_name": c.user_name,
                        "editor_name": c.editor_name,
                        "tool_name": c.tool_name,
                        "args_summary": c.args_summary,
                        "latency_ms": c.latency_ms,
                        "status": c.status,
                    }
                    for c in calls
                ],
            }
        )

    async def users_view(request: Request) -> Response:
        users = await store.list_users(inactive_days=inactive_days)
        summary = await store.adoption_summary(inactive_days=inactive_days)
        return templates.TemplateResponse(
            request,
            "users.html",
            _ctx(request, users=users, summary=summary, page="users"),
        )

    async def tools_view(request: Request) -> Response:
        window_days = _int_query(request, "days", default=7, lo=1, hi=90)
        tool_stats = await store.list_tool_stats(window_days=window_days)
        doc_fetches = await store.list_doc_fetches(window_days=window_days, limit=50)
        return templates.TemplateResponse(
            request,
            "tools.html",
            _ctx(
                request,
                tool_stats=tool_stats,
                doc_fetches=doc_fetches,
                window_days=window_days,
                page="tools",
            ),
        )

    async def searches_view(request: Request) -> Response:
        recent = await store.list_searches(limit=50)
        zero_result = await store.list_zero_result_searches(limit=25)
        return templates.TemplateResponse(
            request,
            "searches.html",
            _ctx(
                request, recent_searches=recent, zero_result_searches=zero_result, page="searches"
            ),
        )

    async def activity_view(request: Request) -> Response:
        calls = await store.list_recent_calls(limit=100)
        return templates.TemplateResponse(
            request,
            "activity.html",
            _ctx(request, calls=calls, page="activity"),
        )

    async def user_detail_view(request: Request) -> Response:
        name = request.path_params["name"]
        detail = await store.get_user(user_name=name, inactive_days=inactive_days)
        if detail is None:
            return HTMLResponse(
                f"<p>User <code>{_html_escape(name)}</code> not found.</p>", status_code=404
            )
        return templates.TemplateResponse(
            request, "user_detail.html", _ctx(request, detail=detail, page="users")
        )

    async def setup_view(request: Request) -> Response:
        base = str(request.base_url).rstrip("/")
        sse_url = f"{base}/sse"
        auth_token: str | None = None
        last_call: str | None = None
        principal = scope_principal(request.scope)
        if (
            auth_enabled
            and auth_store is not None
            and principal is not None
            and principal.user_id != "public"
        ):
            all_tokens = await auth_store.list_tokens(principal.user_id)
            active = [t for t in all_tokens if t["active"]]
            if active:
                auth_token = active[0]["token"]
        if principal is not None and principal.user_id != "public":
            detail = await store.get_user(
                user_name=principal.user_name, inactive_days=inactive_days
            )
            if detail is not None:
                last_call = detail.last_seen
        token_generated = bool(request.query_params.get("token_generated"))
        return templates.TemplateResponse(
            request,
            "setup.html",
            _ctx(
                request,
                sse_url=sse_url,
                page="setup",
                auth_enabled=auth_enabled,
                auth_token=auth_token,
                last_call=last_call,
                token_generated=token_generated,
            ),
        )

    async def palette_api(request: Request) -> Response:
        """Index for the ⌘K command palette: users and quick actions."""
        principal = scope_principal(request.scope)
        is_admin = principal is not None and getattr(principal, "role", "user") == "admin"
        users = await store.list_users(inactive_days=inactive_days)
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "users": [u.user_name for u in users][:50],
                "sse_url": f"{base}/sse",
                "is_admin": is_admin,
            }
        )

    async def setup_last_call_api(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if principal is None or principal.user_id == "public":
            return JSONResponse({"last_call": None, "user": None})
        detail = await store.get_user(user_name=principal.user_name, inactive_days=inactive_days)
        return JSONResponse(
            {
                "last_call": detail.last_seen if detail else None,
                "user": principal.user_name,
            }
        )

    # -- MCP token management (all authenticated users) ----------------------

    async def tokens_view(request: Request) -> Response:
        principal = scope_principal(request.scope)
        tokens: list[dict] = []
        error: str | None = None
        success: str | None = None
        if auth_store is not None and principal is not None and principal.user_id != "public":
            tokens = await auth_store.list_tokens(principal.user_id)
        if request.query_params.get("generated"):
            success = "Token generated successfully."
        if request.query_params.get("revoked"):
            success = "Token revoked."
        if request.query_params.get("error"):
            error = "An error occurred."
        return templates.TemplateResponse(
            request,
            "tokens.html",
            _ctx(request, tokens=tokens, error=error, success=success, page="tokens"),
        )

    async def tokens_generate(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if auth_store is None or principal is None or principal.user_id == "public":
            return RedirectResponse("/dashboard/tokens?error=1", status_code=303)
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        raw_days = str(form.get("expires_in_days", "")).strip()
        expires_in_days: int | None = None
        if raw_days and raw_days != "0":
            try:
                expires_in_days = int(raw_days)
            except ValueError:
                return RedirectResponse("/dashboard/tokens?error=1", status_code=303)
        await auth_store.create_token(principal.user_id, expires_in_days, token_type="mcp")
        next_path = str(form.get("next", "")).strip()
        if next_path == "/dashboard/setup":
            return RedirectResponse("/dashboard/setup?token_generated=1", status_code=303)
        return RedirectResponse("/dashboard/tokens?generated=1", status_code=303)

    async def tokens_revoke(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if auth_store is None or principal is None or principal.user_id == "public":
            return RedirectResponse("/dashboard/tokens?error=1", status_code=303)
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        token = str(form.get("token", "")).strip()
        if token:
            is_admin = getattr(principal, "role", "user") == "admin"
            user_tokens = await auth_store.list_tokens(principal.user_id)
            owned = {t["token"] for t in user_tokens}
            if is_admin or token in owned:
                await auth_store.revoke_token(token)
        return RedirectResponse("/dashboard/tokens?revoked=1", status_code=303)

    # -- Admin: user management (admin only) ---------------------------------

    async def users_admin_view(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if principal is None or getattr(principal, "role", "user") != "admin":
            return _forbidden(request)
        users: list[dict] = []
        error: str | None = None
        success: str | None = None
        if auth_store is not None:
            users = await auth_store.list_users()
        if request.query_params.get("created"):
            success = "User created successfully."
        if request.query_params.get("error") == "duplicate":
            error = "Username already exists."
        elif request.query_params.get("error"):
            error = "An error occurred."
        return templates.TemplateResponse(
            request,
            "users_admin.html",
            _ctx(request, auth_users=users, error=error, success=success, page="users-admin"),
        )

    async def users_admin_create(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if principal is None or getattr(principal, "role", "user") != "admin":
            return _forbidden(request)
        if auth_store is None:
            return RedirectResponse("/dashboard/users-admin?error=1", status_code=303)
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", "")).strip()
        role = str(form.get("role", "user")).strip()
        if role not in ("admin", "user"):
            role = "user"
        if not username or not password:
            return RedirectResponse("/dashboard/users-admin?error=1", status_code=303)
        try:
            await auth_store.create_user(username, password, role)
        except Exception:
            return RedirectResponse("/dashboard/users-admin?error=duplicate", status_code=303)
        return RedirectResponse("/dashboard/users-admin?created=1", status_code=303)

    # -- Standards (corpus health, SQLite-backed) ----------------------------

    _GROUP_ORDER = [
        "",  # root-level files (AGENTS.md, INDEX.md, README.md, ...) share one group
        "core",
        "languages",
        "patterns",
        "workflows",
        "gates",
    ]

    def _group_key(k: str) -> int:
        try:
            return _GROUP_ORDER.index(k)
        except ValueError:
            return len(_GROUP_ORDER)

    def _group_files(files: list) -> list[tuple[str, list]]:
        groups: dict[str, list] = {}
        for fs in files:
            head = fs.relative_path.split("/", 1)[0] if "/" in fs.relative_path else ""
            groups.setdefault(head, []).append(fs)
        items = sorted(groups.items(), key=lambda kv: _group_key(kv[0]))

        # "languages" gets a second level of nesting: one card per language
        # (kotlin, python, ...) holding that language's standards/testing/
        # anti-patterns files, instead of one flat row per file.
        result: list[tuple[str, list]] = []
        for name, group_files in items:
            if name == "languages":
                by_language: dict[str, list] = {}
                for fs in group_files:
                    lang = fs.relative_path.split("/", 2)[1]
                    by_language.setdefault(lang, []).append(fs)
                result.append((name, sorted(by_language.items())))
            else:
                result.append((name, group_files))
        return result

    def _not_found(what: str, value: str) -> Response:
        return HTMLResponse(
            f"<p>{what} <code>{_html_escape(value)}</code> not found.</p>", status_code=404
        )

    def _valid_relative_path(path: str) -> bool:
        if not path or path.startswith("/") or path.endswith("/"):
            return False
        return all(part not in ("", ".", "..") for part in path.split("/"))

    def _infer_kind(relative_path: str) -> str:
        if relative_path.startswith("gates/scripts/") and not relative_path.endswith(".md"):
            return "script"
        return "markdown"

    async def standards_view(request: Request) -> Response:
        from standards_scanner import scan_all

        projects = await scan_all(standards) if standards else []
        return templates.TemplateResponse(
            request,
            "standards.html",
            _ctx(request, page="standards", projects=projects),
        )

    async def standard_detail_view(request: Request) -> Response:
        import base64

        from standards_scanner import scan_project

        name = request.path_params.get("name", "")
        if not standards or not name or "/" in name or "\\" in name:
            return _not_found("Project", name)
        if name not in await standards.list_projects():
            return _not_found("Project", name)

        status = await scan_project(standards, name)
        rows = await standards.list_files(name)
        rows_by_path = {r.relative_path: r for r in rows}

        def _full_content(row) -> str:
            if row.kind == "markdown" and row.frontmatter.strip():
                return f"---\n{row.frontmatter}\n---\n{row.body}"
            return row.body

        raw_by_path = {
            path: base64.b64encode(_full_content(row).encode("utf-8")).decode("ascii")
            for path, row in rows_by_path.items()
        }
        body_by_path = {
            path: base64.b64encode(row.body.encode("utf-8")).decode("ascii")
            for path, row in rows_by_path.items()
        }
        version_by_path = {path: row.version for path, row in rows_by_path.items()}
        kind_by_path = {path: row.kind for path, row in rows_by_path.items()}

        return templates.TemplateResponse(
            request,
            "standard_detail.html",
            _ctx(
                request,
                page="standards",
                status=status,
                groups=_group_files(status.files),
                raw_by_path=raw_by_path,
                body_by_path=body_by_path,
                version_by_path=version_by_path,
                kind_by_path=kind_by_path,
                rule_catalog=_RULE_CATALOG,
            ),
        )

    def _admin_or_forbidden(request: Request) -> Response | None:
        principal = scope_principal(request.scope)
        if principal is None or getattr(principal, "role", "user") != "admin":
            return _forbidden(request)
        return None

    # -- New project wizard --------------------------------------------------
    #
    # The step sequence depends on how many languages were picked, so it is
    # computed rather than hardcoded: six steps for one language, eight for
    # three. _wizard_sequence is the single source of that order - the stepper,
    # the Next and Back buttons and the review page's Edit links all derive from
    # it, so nothing desynchronises when the language selection changes.
    #
    # Each step is a POST that renders the next and carries the whole selection
    # forward in hidden fields. Stateless, so a step can be POSTed to directly -
    # which is why every step re-validates rather than trusting an earlier one.
    #
    # These handlers only parse the form and render. Every scaffolding decision
    # lives in scaffold_service, so the planned MCP tool runs identical rules.

    _WIZARD_BASE = "/dashboard/standards/new-project"

    def _wizard_sequence(state: dict) -> list[dict]:
        """Ordered steps for the current selection: (key, label, action)."""
        import scaffold_service

        steps = [{"key": "languages", "label": "Project & languages", "action": _WIZARD_BASE}]
        steps.append(
            {"key": "base", "label": "Shared rules", "action": f"{_WIZARD_BASE}/rules/base"}
        )
        by_id = {p.id: p for p in scaffold_service.list_languages()}
        for lang_id in state.get("languages", []):
            pack = by_id.get(lang_id)
            if pack is None:
                continue
            steps.append(
                {
                    "key": pack.id,
                    "label": f"{pack.title} rules",
                    "action": f"{_WIZARD_BASE}/rules/{pack.id}",
                }
            )
        steps += [
            {"key": "git", "label": "Git rules", "action": f"{_WIZARD_BASE}/git-rules"},
            {"key": "workflows", "label": "Workflows", "action": f"{_WIZARD_BASE}/workflows"},
            {"key": "review", "label": "Review", "action": f"{_WIZARD_BASE}/review"},
        ]
        for i, step in enumerate(steps, start=1):
            step["number"] = i
        return steps

    def _step_links(state: dict, key: str) -> dict:
        """Where this step's Next and Back buttons point."""
        steps = _wizard_sequence(state)
        index = next((i for i, s in enumerate(steps) if s["key"] == key), 0)
        nxt = steps[index + 1] if index + 1 < len(steps) else None
        prev = steps[index - 1] if index > 0 else None
        return {
            "steps": steps,
            "current_step": steps[index]["number"] if steps else 1,
            "next_action": nxt["action"] if nxt else None,
            "next_label": f"Next: {nxt['label']}" if nxt else "Continue",
            "back_action": prev["action"] if prev else None,
        }

    def _wizard_state(form) -> dict:
        """The selection so far, as carried between steps."""
        return {
            "project": str(form.get("project", "")).strip(),
            "languages": [str(v) for v in form.getlist("language")],
            "placeholders": {
                k[len("placeholder_") :]: str(v).strip()
                for k, v in form.multi_items()
                if k.startswith("placeholder_") and str(v).strip()
            },
            "language_rules": [str(v) for v in form.getlist("language_rule")],
            "git_rules": [str(v) for v in form.getlist("git_rule")],
            "workflows": [str(v) for v in form.getlist("workflow")],
        }

    def _wizard_ctx(request: Request, key: str, state: dict, **extra) -> dict:
        return _ctx(request, page="standards", state=state, **_step_links(state, key), **extra)

    def _rule_sections(pack, *, git: bool):
        """This pack's rule groups, as (label, doc-or-contribution) pairs.

        Git rules are picked on their own step, so they are separated out here
        rather than appearing among the shared rules.
        """
        import scaffold_service

        owner_labels = {
            d.doc: d.display_label for d in scaffold_service.get_base().rule_docs
        }
        sections = []
        for doc in pack.rule_docs:
            if (doc.doc == "core/git.md") != git:
                continue
            sections.append((doc.display_label, doc))
        if not git:
            for contribution in pack.contributions:
                label = owner_labels.get(
                    contribution.contributes_to, contribution.contributes_to
                )
                sections.append((label, contribution))
        return sections

    async def project_new_view(request: Request) -> Response:
        """Step 1. Also serves POST, so Back from step 2 returns here intact."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        import scaffold_service

        if request.method == "POST":
            form = await request.form()
            csrf_err = _validate_csrf_or_403(request, form)
            if csrf_err:
                return csrf_err
            state = _wizard_state(form)
        else:
            state = {
                "project": "",
                "languages": [],
                "placeholders": {},
                "language_rules": [],
                "git_rules": [],
                "workflows": [],
            }
        return templates.TemplateResponse(
            request,
            "wizard_languages.html",
            _wizard_ctx(request, "languages", state, languages=scaffold_service.list_languages()),
        )

    async def _step1_errors(state: dict) -> list[str]:
        import scaffold_service

        errors = []
        if not scaffold_service.is_valid_project_name(state["project"]):
            errors.append("Project name must be letters, digits, dot, dash or underscore.")
        if standards and state["project"] in await standards.list_projects():
            errors.append(f"A project named '{state['project']}' already exists.")
        if not state["languages"]:
            errors.append("Select at least one language.")
        return errors

    def _back_to_step1(request: Request, state: dict, errors: list[str]) -> Response:
        import scaffold_service

        return templates.TemplateResponse(
            request,
            "wizard_languages.html",
            _wizard_ctx(
                request, "languages", state,
                languages=scaffold_service.list_languages(), errors=errors,
            ),
            status_code=400,
        )

    async def project_step_rules(request: Request) -> Response:
        """One handler for every rule step: shared rules and each language."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        import scaffold_service

        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        state = _wizard_state(form)
        pack_id = request.path_params["pack"]

        errors = await _step1_errors(state)
        languages: list = []
        if not errors:
            try:
                base, languages = scaffold_service.resolve_packs(state["languages"])
            except scaffold_service.ScaffoldError as exc:
                errors.append(str(exc))
        if not errors:
            missing = [
                name
                for name in scaffold_service.required_placeholders(languages)
                if not state["placeholders"].get(name)
            ]
            errors += [f"'{name}' is required by the selected languages." for name in missing]
        if errors:
            return _back_to_step1(request, state, errors)

        packs = {p.id: p for p in [base, *languages]}
        pack = packs.get(pack_id)
        if pack is None:
            return _not_found("Rule step", pack_id)

        # First visit pre-ticks this pack's defaults; a return visit keeps what
        # was chosen. Other packs' selections travel untouched in hidden fields.
        selected = set(state["language_rules"])
        if not selected & {r.id for r in pack.all_rules}:
            selected |= pack.default_rule_ids() - {
                r.id for d in pack.rule_docs if d.doc == "core/git.md" for r in d.rules
            }
        state["language_rules"] = sorted(selected)

        sections = _rule_sections(pack, git=False)
        return templates.TemplateResponse(
            request,
            "wizard_rules.html",
            _wizard_ctx(
                request, pack.id, state,
                pack=pack, sections=sections, field="language_rule",
                heading=("Shared rules" if pack.kind == "base" else f"{pack.title} rules"),
                blurb=(
                    "Rules that apply whatever the language."
                    if pack.kind == "base"
                    else f"Rules specific to {pack.title} {pack.language_version}."
                ),
            ),
        )

    async def project_step_git_rules(request: Request) -> Response:
        """Git rules, which come from the base pack."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        import scaffold_service

        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        state = _wizard_state(form)

        base = scaffold_service.get_base()
        sections = _rule_sections(base, git=True)
        if not state["git_rules"]:
            git_ids = {r.id for _, d in sections for r in d.rules}
            state["git_rules"] = sorted(base.default_rule_ids() & git_ids)
        return templates.TemplateResponse(
            request,
            "wizard_rules.html",
            _wizard_ctx(
                request, "git", state,
                pack=base, sections=sections, field="git_rule",
                heading="Git rules",
                blurb="How a change reaches the default branch. These become core/git.md.",
            ),
        )

    async def project_step_workflows(request: Request) -> Response:
        """Required workflows are locked; the extras are optional."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        import scaffold_service

        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        state = _wizard_state(form)

        base = scaffold_service.get_base()
        if not state["workflows"]:
            state["workflows"] = [w.id for w in base.workflows]
        return templates.TemplateResponse(
            request,
            "wizard_workflows.html",
            _wizard_ctx(request, "workflows", state, workflows=base.workflows),
        )

    async def project_step_review(request: Request) -> Response:
        """A dry run of exactly what will be written."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        import scaffold_service

        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        state = _wizard_state(form)

        try:
            base, languages = scaffold_service.resolve_packs(state["languages"])
            documents = scaffold_service.preview(
                state["languages"],
                state["project"],
                state["placeholders"],
                set(state["language_rules"]) | set(state["git_rules"]),
                set(state["workflows"]),
            )
        except scaffold_service.ScaffoldError as exc:
            return HTMLResponse(
                f"<h1>400 Bad Request</h1><p>{_html_escape(str(exc))}</p>", status_code=400
            )

        return templates.TemplateResponse(
            request,
            "wizard_review.html",
            _wizard_ctx(
                request, "review", state,
                base=base, languages=languages, documents=documents,
                # Edit links come from the sequence so they follow the selection.
                rules_action=f"{_WIZARD_BASE}/rules/base",
                workflows_action=f"{_WIZARD_BASE}/workflows",
            ),
        )

    async def project_create(request: Request) -> Response:
        """Create. Every value is re-validated inside the service."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        if not standards:
            return _not_found("Project", "")
        import scaffold_service
        from standards_store import ProjectExists

        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        state = _wizard_state(form)

        principal = scope_principal(request.scope)
        try:
            await scaffold_service.scaffold_project(
                standards,
                languages=state["languages"],
                project=state["project"],
                placeholders=state["placeholders"],
                selected_rule_ids=set(state["language_rules"]) | set(state["git_rules"]),
                workflow_ids=set(state["workflows"]),
                actor=principal.user_name if principal else None,
            )
        except ProjectExists:
            return HTMLResponse(
                f"<h1>409 Conflict</h1><p>A project named "
                f"<code>{_html_escape(state['project'])}</code> already exists.</p>",
                status_code=409,
            )
        except scaffold_service.ScaffoldError as exc:
            return HTMLResponse(
                f"<h1>400 Bad Request</h1><p>{_html_escape(str(exc))}</p>", status_code=400
            )

        return RedirectResponse(f"/dashboard/standards/{state['project']}", status_code=303)

    async def standard_new_view(request: Request) -> Response:
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        name = request.path_params.get("name", "")
        return templates.TemplateResponse(
            request,
            "standard_new.html",
            _ctx(request, page="standards", project=name),
        )

    def _csrf_header_or_403(request: Request) -> Response | None:
        """CSRF check for JSON (fetch) requests: header instead of form field."""
        if dashboard_session is None:
            return None
        cookie_val = dashboard_session.read_csrf_cookie(request.scope)
        header_val = request.headers.get("x-csrf-token")
        if not dashboard_session.validate_csrf(cookie_val, header_val):
            return JSONResponse({"error": "invalid_csrf"}, status_code=403)
        return None

    async def standard_file_api(request: Request) -> Response:
        """POST /api/standards/{name}/file - JSON save for the Edit tab."""
        from standards_scanner import (
            _extract_description,
            _extract_title,
            _file_status,
            _parse_frontmatter,
        )

        principal = scope_principal(request.scope)
        if principal is None or getattr(principal, "role", "user") != "admin":
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not standards:
            return JSONResponse({"error": "not_found"}, status_code=404)

        csrf_err = _csrf_header_or_403(request)
        if csrf_err:
            return csrf_err

        try:
            body_json = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_json"}, status_code=400)

        name = request.path_params["name"]
        path = str(body_json.get("path", ""))
        content = str(body_json.get("content", ""))
        raw_expected_version = body_json.get("expected_version")

        if not _valid_relative_path(path):
            return JSONResponse({"error": "invalid_path"}, status_code=400)

        try:
            expected_version = (
                int(raw_expected_version) if raw_expected_version is not None else None
            )
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_version"}, status_code=400)

        kind = _infer_kind(path)
        if kind == "markdown":
            meta, body_text = _parse_frontmatter(content)
            frontmatter_text = _frontmatter_only(content)
            title = _extract_title(meta, body_text, path.rsplit("/", 1)[-1])
            description = _extract_description(meta)
        else:
            frontmatter_text = ""
            body_text = content
            title = path.rsplit("/", 1)[-1]
            description = ""

        existing = await standards.get_file(name, path)
        is_executable = existing.is_executable if existing else False

        try:
            row = await standards.upsert_file(
                project=name,
                relative_path=path,
                kind=kind,
                title=title,
                description=description,
                frontmatter=frontmatter_text,
                body=body_text,
                is_executable=is_executable,
                expected_version=expected_version,
                updated_by=principal.user_name,
            )
        except VersionConflict as exc:
            return JSONResponse(
                {"error": "version_conflict", "current_version": exc.actual}, status_code=409
            )

        file_status = _file_status(row)
        return JSONResponse(
            {
                "relative_path": file_status.relative_path,
                "title": file_status.title,
                "summary": file_status.summary,
                "indicator": file_status.indicator,
                "frontmatter": file_status.frontmatter,
                "raw_body": file_status.raw_body,
                "version": row.version,
                "rules": [
                    {
                        "rule_id": r.rule_id,
                        "severity": r.severity,
                        "passed": r.passed,
                        "message": r.message,
                    }
                    for r in file_status.rules
                ],
            }
        )

    async def standard_create(request: Request) -> Response:
        """POST /standards/{name}/{path} - create a new doc (full-page form, not the JSON API)."""
        from standards_scanner import _extract_description, _extract_title, _parse_frontmatter

        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        if not standards:
            return _not_found("Project", request.path_params.get("name", ""))
        name = request.path_params["name"]
        path = request.path_params["path"]
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err

        if not _valid_relative_path(path):
            return HTMLResponse("400 Bad Request - invalid file path.", status_code=400)

        content = str(form.get("content", ""))
        kind = _infer_kind(path)
        if kind == "markdown":
            meta, body_text = _parse_frontmatter(content)
            frontmatter_text = _frontmatter_only(content)
            title = _extract_title(meta, body_text, path.rsplit("/", 1)[-1])
            description = _extract_description(meta)
        else:
            frontmatter_text = ""
            body_text = content
            title = path.rsplit("/", 1)[-1]
            description = ""

        principal = scope_principal(request.scope)

        try:
            await standards.upsert_file(
                project=name,
                relative_path=path,
                kind=kind,
                title=title,
                description=description,
                frontmatter=frontmatter_text,
                body=body_text,
                is_executable=False,
                expected_version=None,  # this route only ever creates
                updated_by=principal.user_name if principal else None,
            )
        except VersionConflict:
            return HTMLResponse(
                f"<h1>409 Conflict</h1><p>A file already exists at "
                f"<code>{_html_escape(path)}</code>. "
                f'<a href="/dashboard/standards/{name}">Go back</a> and edit it there instead.</p>',
                status_code=409,
            )

        return RedirectResponse(f"/dashboard/standards/{name}", status_code=303)

    async def project_delete(request: Request) -> Response:
        """Delete a whole project. Irreversible, so the form makes the user type
        the project name and the handler re-checks it server-side."""
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        if not standards:
            return _not_found("Project", request.path_params.get("name", ""))
        name = request.path_params["name"]
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err

        if name not in await standards.list_projects():
            return _not_found("Project", name)

        # The typed confirmation is a real check, not decoration: a mis-aimed
        # POST would otherwise destroy an entire corpus.
        if str(form.get("confirm", "")).strip() != name:
            return HTMLResponse(
                "<h1>400 Bad Request</h1><p>Type the project name exactly to confirm "
                f"deletion of <code>{_html_escape(name)}</code>.</p>"
                f'<p><a href="/dashboard/standards/{name}">Go back</a></p>',
                status_code=400,
            )

        await standards.delete_project(name)
        return RedirectResponse("/dashboard/standards?deleted=1", status_code=303)

    async def standard_delete(request: Request) -> Response:
        forbidden = _admin_or_forbidden(request)
        if forbidden:
            return forbidden
        if not standards:
            return _not_found("Project", request.path_params.get("name", ""))
        name = request.path_params["name"]
        path = request.path_params["path"]
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        await standards.delete_file(name, path)
        return RedirectResponse(f"/dashboard/standards/{name}", status_code=303)

    static = Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return [
        Route("/", endpoint=dashboard_view, methods=["GET"]),
        Route("/users", endpoint=users_view, methods=["GET"]),
        Route("/tools", endpoint=tools_view, methods=["GET"]),
        Route("/searches", endpoint=searches_view, methods=["GET"]),
        Route("/activity", endpoint=activity_view, methods=["GET"]),
        Route("/standards", endpoint=standards_view, methods=["GET"]),
        # Must precede /standards/{name}: Starlette matches in order, so these
        # literal paths would otherwise be swallowed by the {name} patterns.
        Route("/standards/new-project", endpoint=project_new_view, methods=["GET", "POST"]),
        # One handler serves every rule step: base plus each language pack, so
        # adding a fourth language needs no route change.
        Route(
            "/standards/new-project/rules/{pack}",
            endpoint=project_step_rules,
            methods=["POST"],
        ),
        Route(
            "/standards/new-project/git-rules",
            endpoint=project_step_git_rules,
            methods=["POST"],
        ),
        Route(
            "/standards/new-project/workflows",
            endpoint=project_step_workflows,
            methods=["POST"],
        ),
        Route("/standards/new-project/review", endpoint=project_step_review, methods=["POST"]),
        Route("/standards/new-project/create", endpoint=project_create, methods=["POST"]),
        Route("/standards/{name}/new", endpoint=standard_new_view, methods=["GET"]),
        # Must precede the {path:path} routes, which would otherwise swallow it.
        Route("/standards/{name}/delete-project", endpoint=project_delete, methods=["POST"]),
        Route("/standards/{name}/{path:path}/delete", endpoint=standard_delete, methods=["POST"]),
        Route("/standards/{name}/{path:path}", endpoint=standard_create, methods=["POST"]),
        Route("/standards/{name}", endpoint=standard_detail_view, methods=["GET"]),
        Route("/api/standards/{name}/file", endpoint=standard_file_api, methods=["POST"]),
        Route("/setup", endpoint=setup_view, methods=["GET"]),
        Route("/api/me/last-call", endpoint=setup_last_call_api, methods=["GET"]),
        Route("/api/palette", endpoint=palette_api, methods=["GET"]),
        Route("/api/summary", endpoint=summary_api, methods=["GET"]),
        Route("/tokens", endpoint=tokens_view, methods=["GET"]),
        Route("/tokens/generate", endpoint=tokens_generate, methods=["POST"]),
        Route("/tokens/revoke", endpoint=tokens_revoke, methods=["POST"]),
        Route("/users-admin", endpoint=users_admin_view, methods=["GET"]),
        Route("/users-admin/create", endpoint=users_admin_create, methods=["POST"]),
        Route("/users/{name}", endpoint=user_detail_view, methods=["GET"]),
        static,
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _int_query(request: Request, key: str, *, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get(key)
    if raw is None:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _frontmatter_only(content: str) -> str:
    """Extract the raw YAML text between the --- markers, without parsing it."""
    import re

    m = re.match(r"^---\s*\n(.*?\n)---\s*\n", content, re.DOTALL)
    return m.group(1).rstrip("\n") if m else ""


# rule_id -> (severity, one-line description) - shown in the detail page's
# rule-catalog reference block so severity badges have somewhere to link.
_RULE_CATALOG: dict[str, tuple[str, str]] = {
    "required-file": (
        "hard",
        "A file every standards project must ship "
        "(AGENTS.md, ARCHITECTURE.md, core/*, gates/README.md).",
    ),
    "required-workflow": (
        "hard",
        "A workflow doc every project must ship (new-feature, bug-fix, security-fix, refactor).",
    ),
    "fm-title": ("hard", "The file's YAML frontmatter must set a `title`."),
    "fm-description": ("hard", "The file's YAML frontmatter must set a `description`."),
    "content-length": (
        "soft",
        "Body content (excluding frontmatter) should be at least 80 characters.",
    ),
    "structured-content": ("soft", "Body should contain at least one fenced code block or table."),
    "gate-executable": (
        "soft",
        "Shell scripts under gates/scripts/ should have their executable bit set.",
    ),
}
