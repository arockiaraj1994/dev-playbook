/* ⌘K command palette: jump to pages, projects, users; quick actions.
   Static page index + lazily fetched /dashboard/api/palette data. */
(function () {
  var overlay = document.getElementById("cmd-palette");
  var input = document.getElementById("palette-input");
  var list = document.getElementById("palette-list");
  var trigger = document.getElementById("palette-trigger");
  if (!overlay || !input || !list) return;

  var PAGES = [
    { label: "Dashboard", hint: "page", href: "/dashboard/" },
    { label: "Standards", hint: "page", href: "/dashboard/projects" },
    { label: "Users & adoption", hint: "page", href: "/dashboard/users" },
    { label: "Tokens", hint: "page", href: "/dashboard/tokens" },
    { label: "Setup", hint: "page", href: "/dashboard/setup" },
    { label: "Guide", hint: "page", href: "/dashboard/guide" },
    { label: "Tools & rule popularity", hint: "page", href: "/dashboard/tools" },
    { label: "Search queries", hint: "page", href: "/dashboard/searches" },
    { label: "Recent activity", hint: "page", href: "/dashboard/activity" },
  ];

  var items = [];        /* full index: PAGES + fetched entries + actions */
  var visible = [];      /* current filtered items */
  var selected = 0;
  var fetched = false;

  function csrfToken() {
    var el = document.querySelector('#logout-form input[name="_csrf"]');
    return el ? el.value : "";
  }

  function baseActions(data) {
    var actions = [
      { label: "Switch theme", hint: "action", run: function () {
          var btn = document.getElementById("theme-toggle");
          if (btn) btn.click();
        } },
    ];
    if (data && data.sse_url && navigator.clipboard) {
      actions.push({ label: "Copy SSE URL", hint: "action", run: function () {
        navigator.clipboard.writeText(data.sse_url);
      } });
    }
    if (data && data.is_admin) {
      actions.push({ label: "Reload standards corpus", hint: "action", run: function () {
        var form = document.createElement("form");
        form.method = "post";
        form.action = "/dashboard/reload";
        var f = document.createElement("input");
        f.type = "hidden"; f.name = "_csrf"; f.value = csrfToken();
        form.appendChild(f);
        document.body.appendChild(form);
        form.submit();
      } });
    }
    if (document.getElementById("logout-form")) {
      actions.push({ label: "Sign out", hint: "action", run: function () {
        document.getElementById("logout-form").submit();
      } });
    }
    return actions;
  }

  function buildIndex(data) {
    items = PAGES.slice();
    if (data) {
      (data.projects || []).forEach(function (p) {
        items.push({ label: p, hint: "standards project", href: "/dashboard/projects/" + encodeURIComponent(p) });
      });
      (data.users || []).forEach(function (u) {
        items.push({ label: u, hint: "user", href: "/dashboard/users/" + encodeURIComponent(u) });
      });
    }
    items = items.concat(baseActions(data));
  }

  function ensureIndex() {
    if (fetched) return;
    fetched = true;
    buildIndex(null);
    fetch("/dashboard/api/palette", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { buildIndex(data); filter(); })
      .catch(function () { /* static index still works */ });
  }

  /* Score: prefix (3) > word-start (2) > substring (1) > subsequence (0.5) */
  function score(label, q) {
    var l = label.toLowerCase();
    if (l.indexOf(q) === 0) return 3;
    var wordStart = l.split(/[\s\-_/]+/).some(function (w) { return w.indexOf(q) === 0; });
    if (wordStart) return 2;
    if (l.indexOf(q) !== -1) return 1;
    var qi = 0;
    for (var i = 0; i < l.length && qi < q.length; i++) {
      if (l[i] === q[qi]) qi++;
    }
    return qi === q.length ? 0.5 : -1;
  }

  function filter() {
    var q = input.value.trim().toLowerCase();
    if (!q) {
      visible = items.slice(0, 12);
    } else {
      visible = items
        .map(function (it) { return { it: it, s: score(it.label, q) }; })
        .filter(function (x) { return x.s >= 0; })
        .sort(function (a, b) { return b.s - a.s; })
        .slice(0, 12)
        .map(function (x) { return x.it; });
    }
    selected = 0;
    render();
  }

  function render() {
    list.innerHTML = "";
    if (!visible.length) {
      var emptyEl = document.createElement("div");
      emptyEl.className = "palette-empty";
      emptyEl.textContent = "No matches. Try a page, project, or user name.";
      list.appendChild(emptyEl);
      input.removeAttribute("aria-activedescendant");
      return;
    }
    visible.forEach(function (it, i) {
      var row = document.createElement("div");
      row.className = "palette-item" + (i === selected ? " selected" : "");
      row.id = "palette-item-" + i;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", i === selected ? "true" : "false");
      var label = document.createElement("span");
      label.className = "palette-item-label";
      label.textContent = it.label;
      var hint = document.createElement("span");
      hint.className = "palette-item-hint";
      hint.textContent = it.hint;
      row.appendChild(label);
      row.appendChild(hint);
      row.addEventListener("mousemove", function () {
        if (selected !== i) { selected = i; render(); }
      });
      row.addEventListener("click", function () { activate(it); });
      list.appendChild(row);
    });
    input.setAttribute("aria-activedescendant", "palette-item-" + selected);
    var sel = list.children[selected];
    if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
  }

  function activate(it) {
    close();
    if (it.href) { window.location.href = it.href; }
    else if (it.run) { it.run(); }
  }

  var lastFocus = null;

  function open() {
    ensureIndex();
    lastFocus = document.activeElement;
    overlay.hidden = false;
    input.value = "";
    filter();
    input.focus();
  }

  function close() {
    overlay.hidden = true;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function isOpen() { return !overlay.hidden; }

  if (trigger) trigger.addEventListener("click", open);

  overlay.addEventListener("mousedown", function (e) {
    if (e.target === overlay) close();
  });

  input.addEventListener("input", filter);
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selected = Math.min(selected + 1, visible.length - 1);
      render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selected = Math.max(selected - 1, 0);
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (visible[selected]) activate(visible[selected]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      e.preventDefault(); /* focus stays trapped in the input */
    }
  });

  document.addEventListener("keydown", function (e) {
    var mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      isOpen() ? close() : open();
      return;
    }
    if (isOpen()) return;
    if (e.key === "/" && !mod && !e.altKey) {
      var t = e.target;
      var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
      if (tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable)) return;
      e.preventDefault();
      open();
    }
  });
})();
