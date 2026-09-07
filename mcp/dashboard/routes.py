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

if TYPE_CHECKING:
    from auth import AuthStore
    from loader import RulesStore
    from session import DashboardSession

_DASHBOARD_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _DASHBOARD_DIR / "templates"
_STATIC_DIR = _DASHBOARD_DIR / "static"


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["since"] = _since_filter
    templates.env.filters["short_dt"] = _short_dt_filter
    return templates


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
    rules_store: RulesStore | None = None,
    rules_root: Path | None = None,
    requirements_cache: object | None = None,
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

    async def guide_view(request: Request) -> Response:
        return templates.TemplateResponse(request, "guide.html", _ctx(request, page="guide"))

    async def palette_api(request: Request) -> Response:
        """Index for the ⌘K command palette: projects, users, quick-action data."""
        principal = scope_principal(request.scope)
        is_admin = principal is not None and getattr(principal, "role", "user") == "admin"
        projects: list[str] = []
        req_projects: list[str] = []
        if rules_store is not None:
            try:
                projects = list(rules_store.projects(corpus="standards"))
                req_projects = list(rules_store.projects(corpus="requirements"))
            except TypeError:
                projects = list(rules_store.projects())
        users = await store.list_users(inactive_days=inactive_days)
        base = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "projects": projects,
                "requirement_projects": req_projects,
                "users": [u.user_name for u in users][:50],
                "sse_url": f"{base}/sse",
                "is_admin": is_admin,
            }
        )

    # -- Standards + Requirements (corpus health) ----------------------------

    def _score_all_projects() -> list:
        """Score every standards project. Returns ProjectStatus list."""
        if rules_store is None or rules_root is None:
            return []
        from quality import score_project

        projects = (
            rules_store.projects(corpus="standards")
            if hasattr(rules_store, "projects")
            else rules_store.projects()
        )
        return [score_project(p, rules_store, rules_root / p) for p in projects]

    async def projects_view(request: Request) -> Response:
        statuses = _score_all_projects()
        linked = await store.requirement_linked_rate(window_days=30)
        return templates.TemplateResponse(
            request,
            "projects.html",
            _ctx(
                request,
                page="projects",
                projects=statuses,
                rules_loaded=rules_store is not None,
                requirement_linked_pct=linked,
                corpus="standards",
            ),
        )

    async def requirements_view(request: Request) -> Response:
        prd_rows = _requirement_rows()
        projects = _requirement_project_rows(prd_rows)
        linked = await store.requirement_linked_rate(window_days=30)
        return templates.TemplateResponse(
            request,
            "requirements.html",
            _ctx(
                request,
                page="requirements",
                projects=projects,
                rules_loaded=rules_store is not None,
                requirement_linked_pct=linked,
            ),
        )

    async def requirement_detail_view(request: Request) -> Response:
        name = request.path_params.get("name", "")
        if rules_store is None:
            return HTMLResponse("<p>Rules store unavailable.</p>", status_code=503)
        known = (
            rules_store.projects(corpus="requirements") if hasattr(rules_store, "projects") else []
        )
        if name not in known:
            return HTMLResponse(
                f"<p>Requirements project <code>{name}</code> not found.</p>",
                status_code=404,
            )
        prd_rows = [r for r in _requirement_rows() if r["project"] == name]
        # Project-level indicator: worst of its PRDs.
        if any(r["indicator"] == "red" for r in prd_rows):
            indicator = "red"
        elif any(r["indicator"] == "amber" for r in prd_rows):
            indicator = "amber"
        else:
            indicator = "green"
        return templates.TemplateResponse(
            request,
            "requirement_detail.html",
            _ctx(
                request,
                page="requirements",
                project=name,
                prds=prd_rows,
                indicator=indicator,
            ),
        )

    def _requirement_project_rows(prd_rows: list[dict]) -> list[dict]:
        """Aggregate per-PRD rows into Standards-style per-project rows."""
        by_project: dict[str, list[dict]] = {}
        for row in prd_rows:
            by_project.setdefault(row["project"], []).append(row)

        # Also surface requirements projects that have no PRDs yet.
        if rules_store is not None and hasattr(rules_store, "projects"):
            for name in rules_store.projects(corpus="requirements"):
                by_project.setdefault(name, [])

        out: list[dict] = []
        for project in sorted(by_project):
            rows = by_project[project]
            counts = {"red": 0, "amber": 0, "green": 0}
            story_total = 0
            targets_ok = 0
            targets_n = 0
            for r in rows:
                counts[r["indicator"]] = counts.get(r["indicator"], 0) + 1
                story_total += int(r.get("story_total") or 0)
                # Weight coverage by stories that declared targets.
                cov = int(r.get("targets_coverage") or 0)
                st = int(r.get("story_total") or 0)
                if st:
                    targets_n += st
                    targets_ok += round(st * cov / 100)
            if counts["red"]:
                indicator = "red"
            elif counts["amber"]:
                indicator = "amber"
            else:
                indicator = "green"
            coverage = round(100 * targets_ok / targets_n) if targets_n else 0
            out.append(
                {
                    "project": project,
                    "prd_total": len(rows),
                    "story_total": story_total,
                    "counts": counts,
                    "indicator": indicator,
                    "targets_coverage": coverage,
                }
            )
        return out

    def _requirement_rows() -> list[dict]:
        if rules_store is None:
            return []
        from requirement_rules import validate_requirement_docs

        prds = [d for d in rules_store.all_docs(corpus="requirements") if d.doc_type == "prd"]
        standards = rules_store.all_docs(corpus="standards")
        hard, soft = validate_requirement_docs(
            rules_store.all_docs(corpus="requirements"), standards
        )
        issues_by_path: dict[str, list[str]] = {}
        for _proj, _rule, msg in hard + soft:
            # msg starts with relative_path:
            path = msg.split(":", 1)[0].strip()
            issues_by_path.setdefault(path, []).append(msg)

        rows: list[dict] = []
        for prd in sorted(prds, key=lambda d: d.name):
            stories = rules_store.stories_of(prd)
            status_counts = {"draft": 0, "approved": 0, "shipped": 0}
            targets_ok = 0
            targets_total = 0
            for s in stories:
                st = s.metadata.get("status") or "draft"
                if isinstance(st, str) and st in status_counts:
                    status_counts[st] += 1
                else:
                    status_counts["draft"] += 1
                targets = s.metadata.get("targets") or []
                if isinstance(targets, list) and targets:
                    targets_total += 1
                    # Consider covered if no soft failure mentioning this story path
                    if not any(
                        s.relative_path in m
                        for m in issues_by_path.get(s.relative_path, [])
                        if "targets" in m
                    ):
                        # simpler: count stories with non-empty targets as attempted
                        targets_ok += 1
            prd_issues = issues_by_path.get(prd.relative_path, [])
            story_issues = [m for s in stories for m in issues_by_path.get(s.relative_path, [])]
            all_issues = prd_issues + story_issues
            if any(":" in m and m.split(":")[0] for m in all_issues) and any(
                x[1].startswith("prd.") or x[1].startswith("story.")
                for x in hard
                if prd.relative_path in x[2] or any(s.relative_path in x[2] for s in stories)
            ):
                indicator = "red"
            elif all_issues:
                indicator = "amber"
            else:
                indicator = "green"

            # Refine: hard failures → red, soft only → amber
            has_hard = any(
                prd.relative_path in x[2] or any(s.relative_path in x[2] for s in stories)
                for x in hard
            )
            has_soft = any(
                prd.relative_path in x[2] or any(s.relative_path in x[2] for s in stories)
                for x in soft
            )
            if has_hard:
                indicator = "red"
            elif has_soft:
                indicator = "amber"
            else:
                indicator = "green"

            coverage = round(100 * targets_ok / targets_total) if targets_total else 0
            owner = prd.metadata.get("owner") if isinstance(prd.metadata.get("owner"), str) else ""
            title = (
                prd.metadata.get("title")
                if isinstance(prd.metadata.get("title"), str)
                else prd.name
            )
            status = (
                prd.metadata.get("status")
                if isinstance(prd.metadata.get("status"), str)
                else "draft"
            )
            rows.append(
                {
                    "id": prd.name,
                    "title": title,
                    "status": status,
                    "owner": owner or " - ",
                    "story_counts": status_counts,
                    "story_total": len(stories),
                    "indicator": indicator,
                    "targets_coverage": coverage,
                    "project": prd.project,
                }
            )
        return rows

    async def reload_requirements(request: Request) -> Response:
        principal = scope_principal(request.scope)
        if principal is None or getattr(principal, "role", "user") != "admin":
            return _forbidden(request)
        form = await request.form()
        csrf_err = _validate_csrf_or_403(request, form)
        if csrf_err:
            return csrf_err
        if requirements_cache is None or not hasattr(requirements_cache, "force_reload"):
            return RedirectResponse("/dashboard/requirements?error=reload", status_code=303)
        await requirements_cache.force_reload()  # type: ignore[union-attr]
        return RedirectResponse("/dashboard/requirements?reloaded=1", status_code=303)

    async def project_detail_view(request: Request) -> Response:
        name = request.path_params.get("name", "")
        if rules_store is None or rules_root is None:
            return HTMLResponse("<p>Rules store unavailable.</p>", status_code=503)
        if (
            name not in rules_store.projects(corpus="standards")
            and name not in rules_store.projects()
        ):
            return HTMLResponse(
                f"<p>Project <code>{name}</code> not found.</p>",
                status_code=404,
            )
        from quality import score_project

        status = score_project(name, rules_store, rules_root / name)
        # Group files by their top-level folder for display.
        groups: dict[str, list] = {}
        for fs in status.files:
            head = (
                fs.relative_path.split("/", 1)[0] if "/" in fs.relative_path else fs.relative_path
            )
            groups.setdefault(head, []).append(fs)
        # Stable ordering of groups.
        group_order = [
            "AGENTS.md",
            "INDEX.md",
            "core",
            "architecture",
            "languages",
            "patterns",
            "skills",
            "workflows",
            "gates",
        ]

        def _gkey(k: str) -> int:
            try:
                return group_order.index(k)
            except ValueError:
                return len(group_order)

        ordered_groups = [(k, groups[k]) for k in sorted(groups, key=_gkey)]

        return templates.TemplateResponse(
            request,
            "project_detail.html",
            _ctx(
                request,
                page="projects",
                status=status,
                groups=ordered_groups,
            ),
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

    static = Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return [
        Route("/", endpoint=dashboard_view, methods=["GET"]),
        Route("/users", endpoint=users_view, methods=["GET"]),
        Route("/tools", endpoint=tools_view, methods=["GET"]),
        Route("/searches", endpoint=searches_view, methods=["GET"]),
        Route("/activity", endpoint=activity_view, methods=["GET"]),
        Route("/setup", endpoint=setup_view, methods=["GET"]),
        Route("/guide", endpoint=guide_view, methods=["GET"]),
        Route("/projects", endpoint=projects_view, methods=["GET"]),
        Route("/projects/{name}", endpoint=project_detail_view, methods=["GET"]),
        Route("/requirements", endpoint=requirements_view, methods=["GET"]),
        Route("/requirements/{name}", endpoint=requirement_detail_view, methods=["GET"]),
        Route("/reload", endpoint=reload_requirements, methods=["POST"]),
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
