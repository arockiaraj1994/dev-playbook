/* standard_detail.js - four-tab file viewer (Formatted/Source/Code/Edit)
 * for the standards detail page. Vendored libs (marked, DOMPurify, Prism,
 * CodeMirror) are loaded as plain scripts before this file and expose
 * globals: window.marked, window.DOMPurify, window.Prism, window.CodeMirror.
 */
(function () {
  "use strict";

  function b64ToUtf8(b64) {
    if (!b64) return "";
    try {
      var binary = atob(b64);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new TextDecoder("utf-8").decode(bytes);
    } catch (e) {
      console.error("standard_detail: failed to decode base64 content", e);
      return "";
    }
  }

  function utf8ToB64(str) {
    var bytes = new TextEncoder().encode(str);
    var binary = "";
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  if (window.DOMPurify) {
    // Let hand-typed annotation wrappers survive sanitization.
    window.DOMPurify.setConfig({ ADD_ATTR: ["data-annotation"] });
  }

  function renderFormatted(card, container) {
    var bodyMd = b64ToUtf8(card.dataset.bodyB64 || "");
    var html = window.marked ? window.marked.parse(bodyMd) : bodyMd;
    var clean = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
    container.innerHTML = clean;
  }

  function renderSource(card, pre) {
    pre.textContent = b64ToUtf8(card.dataset.rawB64 || "");
  }

  function renderCode(card, codeEl) {
    var raw = b64ToUtf8(card.dataset.rawB64 || "");
    codeEl.textContent = raw;
    codeEl.className = card.dataset.kind === "script" ? "language-bash" : "language-markdown";
    if (window.Prism) window.Prism.highlightElement(codeEl);
  }

  function csrfToken(card) {
    var form = card.querySelector('form[action$="/delete"] input[name="_csrf"]');
    return form ? form.value : "";
  }

  function initEditor(card, mount) {
    if (!window.CodeMirror) return null;
    var raw = b64ToUtf8(card.dataset.rawB64 || "");
    var mode = card.dataset.kind === "script" ? "shell" : "markdown";
    return window.CodeMirror(mount, {
      value: raw,
      mode: mode,
      lineNumbers: true,
      lineWrapping: true,
      theme: "default",
    });
  }

  function initCard(card) {
    var tabs = card.querySelectorAll(".sd-tab");
    var panels = {
      formatted: card.querySelector(".sd-panel-formatted"),
      source: card.querySelector(".sd-panel-source"),
      code: card.querySelector(".sd-panel-code"),
      edit: card.querySelector(".sd-panel-edit"),
    };
    var rendered = { formatted: false, source: false, code: false, edit: false };
    var cmInstance = null;

    function activate(tabName) {
      tabs.forEach(function (t) {
        t.classList.toggle("is-active", t.dataset.tab === tabName);
      });
      Object.keys(panels).forEach(function (name) {
        var panel = panels[name];
        if (!panel) return;
        panel.hidden = name !== tabName;
        panel.classList.toggle("is-active", name === tabName);
      });

      if (!rendered[tabName]) {
        if (tabName === "formatted" && panels.formatted) {
          renderFormatted(card, panels.formatted);
        } else if (tabName === "source" && panels.source) {
          renderSource(card, panels.source.querySelector("pre"));
        } else if (tabName === "code" && panels.code) {
          renderCode(card, panels.code.querySelector("code"));
        } else if (tabName === "edit" && panels.edit) {
          var mount = panels.edit.querySelector(".sd-editor-mount");
          cmInstance = initEditor(card, mount);
        }
        rendered[tabName] = true;
      }
      if (tabName === "edit" && cmInstance) {
        setTimeout(function () { cmInstance.refresh(); }, 0);
      }
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function (e) {
        e.preventDefault();
        activate(tab.dataset.tab);
      });
    });

    // Pre-render the default active tab (Formatted for markdown, Source for scripts).
    var initialTab = card.querySelector(".sd-tab.is-active");
    if (initialTab) activate(initialTab.dataset.tab);

    // Edit tab: Save / Cancel.
    if (panels.edit) {
      var saveBtn = panels.edit.querySelector(".sd-save");
      var cancelBtn = panels.edit.querySelector(".sd-cancel");
      var status = panels.edit.querySelector(".sd-save-status");

      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          if (cmInstance) cmInstance.setValue(b64ToUtf8(card.dataset.rawB64 || ""));
          status.textContent = "";
          status.className = "sd-save-status";
        });
      }

      if (saveBtn) {
        saveBtn.addEventListener("click", function () {
          if (!cmInstance) return;
          var content = cmInstance.getValue();
          var project = card.dataset.project;
          var path = card.dataset.path;
          var version = parseInt(card.dataset.version, 10);

          status.textContent = "Saving…";
          status.className = "sd-save-status";
          saveBtn.disabled = true;

          fetch("/dashboard/api/standards/" + encodeURIComponent(project) + "/file", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken(card),
            },
            body: JSON.stringify({ path: path, content: content, expected_version: version }),
          })
            .then(function (resp) {
              return resp.json().then(function (data) {
                return { ok: resp.ok, status: resp.status, data: data };
              });
            })
            .then(function (result) {
              saveBtn.disabled = false;
              if (!result.ok) {
                if (result.status === 409) {
                  status.textContent =
                    "Someone else changed this file (now at version " +
                    result.data.current_version +
                    "). Reload the page to see the latest version before retrying.";
                } else {
                  status.textContent = "Save failed: " + (result.data.error || result.status);
                }
                status.className = "sd-save-status is-error";
                return;
              }
              // Update card state from the response so Source/Code/Formatted/
              // frontmatter/rules reflect the save without a full page reload.
              card.dataset.version = String(result.data.version);
              card.dataset.rawB64 = utf8ToB64(content);
              card.dataset.bodyB64 = utf8ToB64(result.data.raw_body || "");
              rendered.formatted = false;
              rendered.source = false;
              rendered.code = false;
              status.textContent = "Saved.";
              status.className = "sd-save-status is-ok";
              activate("edit");
              // Force re-render of the other tabs next time they're opened.
              ["formatted", "source", "code"].forEach(function (name) {
                rendered[name] = false;
              });
            })
            .catch(function (err) {
              saveBtn.disabled = false;
              status.textContent = "Save failed: " + err.message;
              status.className = "sd-save-status is-error";
            });
        });
      }
    }
  }

  document.querySelectorAll(".file-card[data-project]").forEach(initCard);
})();
