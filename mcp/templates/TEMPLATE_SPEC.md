# Playbook template pack format

A project is composed from the **base pack** plus one or more **language packs**.
This file is the contract: anything conforming to it loads, whether it ships
bundled here or arrives from a synced template repository.

## Why packs rather than whole templates

Every language needs an `AGENTS.md`, a `core/guardrails.md` and an
`ARCHITECTURE.md`. If each language shipped its own copy, selecting two languages
would mean two packs writing the same file. So the base pack **owns** the shared
documents and each language pack **contributes** its rules into them.

## Layout

```
base/
  pack.yaml                    kind: base
  rules/*.yaml                 owns core/guardrails.md, core/git.md,
                               core/definition-of-done.md, ARCHITECTURE.md
  docs/**                      AGENTS.md, INDEX.md, README.md,
                               core/glossary.md, gates/README.md
  workflows/*.md               required and optional, flagged in frontmatter
languages/<id>/
  pack.yaml                    kind: language
  rules/*.yaml                 contributions, plus any doc it owns outright
  docs/languages/<id>/**       standards, testing
  docs/patterns/<id>/**        namespaced, or two languages collide
  docs/gates/scripts/verify-<id>.sh
```

Packs are discovered by globbing for `pack.yaml` **at any depth** under a search
root, so both layouts work:

```
languages/java/pack.yaml           # one version per language
languages/java/21/pack.yaml        # several versions per language
```

## Search order

`templates_source.py` searches, highest precedence first:

1. `$MCP_TEMPLATE_CACHE` (default `~/.cache/dev-playbook-templates`) - a synced checkout
2. the bundled `mcp/templates/` directory

First match by pack `id` wins, so a synced copy shadows the bundled one and the
bundled set remains the offline fallback. A malformed pack is logged and skipped;
it never prevents the others from loading.

### Keeping your own packs in a private repo

The cache root is how a team keeps its real rules out of this public repo while
the bundled packs stay as the generic fallback:

```bash
git clone git@github.com:<you>/<private-templates>.git ~/.cache/dev-playbook-templates
# or anywhere, with MCP_TEMPLATE_CACHE=/path/to/checkout
```

- **Mirror this layout** at the repo root: `base/pack.yaml`,
  `languages/<id>/pack.yaml`. Discovery globs `pack.yaml` at any depth, so any
  `pack.yaml` in the repo is picked up, test fixtures included.
- **Overrides are whole packs.** A private `java` replaces the bundled `java`
  completely - rules, docs, gate script and workflows - so copy the bundled pack
  and edit it rather than shipping only the rules you changed. A partial pack
  loads, and then scaffolds red.
- **Keep the base pack's id as `base`.** Only one base is used, and it is the
  first found. Under a different id both load and the private one wins on search
  order alone.
- **Bump `template_version`** so scaffolded projects record which content they
  came from.
- **No restart needed.** Packs are re-read on every call, so `git pull` in the
  checkout takes effect on the next scaffold. Projects already scaffolded are
  stored rows and do not change.
- **Check the server log after a pull.** A broken private pack does not fail
  loudly: it is skipped and the bundled pack of the same id takes its place.

Nothing syncs the checkout for you yet; pull it yourself. The test suite points
`MCP_TEMPLATE_CACHE` at an empty path, so a local overlay never changes its
results.

## `pack.yaml`

```yaml
id: java                     # unique; matches the directory name by convention
kind: language               # base | language
title: Java                  # shown in the picker
language: java               # grouping key       (language packs only)
language_version: "21"       # the version axis   (language packs only)
template_version: "1.0.0"    # semver of this pack's content
gate: gates/scripts/verify-java.sh   # required for a language pack
description: >-
  One or two lines shown under the title.
placeholders:                # names the user must supply; "project" is implicit
  - package
```

`template_version` is what makes update detection possible: it is recorded on
every project the pack contributes to.

## Rule files

A rule file either **owns** a document or **contributes** to one.

### Owning

```yaml
doc: core/guardrails.md        # relative path to generate
label: Everyday rules          # category name shown in the wizard's rule picker
merge: by-group                # by-group | by-language
title: Guardrails - {{project}}
description: Always-on MUST / MUST NOT rules.
tags: [guardrails, security]
intro: |                       # optional markdown before the rules
  These rules are non-negotiable.
groups:                        # at least one; defines heading order
  - id: must
    title: MUST
  - id: must_not
    title: MUST NOT
outro: |                       # optional markdown after the rule table
  See `core/definition-of-done.md`.
rules:
  - id: no-hardcoded-secrets   # unique within the pack
    group: must_not            # must match a declared group id
    severity: hard             # hard | soft - mirrors the scanner's severities
    title: No hardcoded secrets
    body: >-
      Credentials must never appear in source or committed configuration.
    default: true              # pre-ticked in the picker (default: true)
    locked: false              # true = cannot be unticked (implies default)
    source: OWASP C2           # optional attribution, shown in the rule table
    help: >-                   # optional long-form help, wizard picker only
      A secret in source is a secret in every clone, fork and CI log, and git
      history keeps it long after the line is deleted.
    example:                   # optional worked pair, wizard picker only
      lang: python             # fence hint; defaults to the pack's language
      caption: ...             # optional one-line framing
      bad: |
        STRIPE_KEY = "sk_live_4eC39Hq..."
      good: |
        STRIPE_KEY = os.environ["STRIPE_KEY"]
```

### Contributing

```yaml
contributes_to: ARCHITECTURE.md
section: Java                  # heading used when more than one pack contributes
section_intro: |               # by-language only: rendered under the heading
  ### Modules
  | Package | Purpose |
  | --- | --- |
section_outro: |               # by-language only
  ### Data flow
rules:
  - id: domain-has-no-framework-imports
    group: dependency          # must be a group the OWNER declared
    severity: hard
    title: Domain is plain Java
    body: ...
```

### Merge modes

- **`by-group`** - groups outermost. The owner's rules render first with no
  subheading, then one `### <section>` block per contributing pack. When only one
  language is selected the subheadings are dropped, so a single-language project
  reads as though packs did not exist.
- **`by-language`** - sections outermost, groups nested inside. The right shape
  when the content itself is per-language, as a module layout is.

### Writing a rule title

The `title` appears twice: as the checkbox label in the wizard, and as the bold
lead-in in the generated markdown (`- **<title>.** <body>`). Write it so a
mid-level developer reads it once and knows what to do:

- **State the action**, don't name the concept. "Don't expose internal classes to
  callers", not "Boundaries own their types".
- **Avoid pattern vocabulary** — port, adapter, anemic, boundary — unless the same
  line explains it.
- **No aphorisms.** "Shared code is a decision, not an accident" sounds good and
  tells you nothing.
- **At most 60 characters**, so it fits one line on a checkbox, and never a single
  bare word.
- Put the reasoning, the reference and the detail in `body`, which is where there
  is room for it.

A test enforces the length and word-count limits over every bundled pack.

### Writing rule help

`help` and `example` are **wizard-only**. They are shown in the rule picker's help
popup and are never rendered into a generated document — `body` is what ships, and
`source_hash` provenance depends on that output not moving. A byte-identity test
enforces this.

The popup falls back to the rule's own `body` when `help` is absent, so a pack
without help still renders correctly; the affordance is never dead. Write:

- **`help`** — two to four sentences answering what the rule means, what goes
  wrong without it, and how to satisfy it. Say the failure mode concretely; that
  is the part `body` has no room for.
- **`example`** — the wrong way and the right way, as short as they can be while
  still being real code. Either side may be omitted when only one is worth
  showing. `lang` defaults to the pack's `language`; base-pack rules are
  language-neutral, so they use `text` with pseudocode or shell.

Coverage is enforced per pack: rules in `base`, `java`, `typescript` and `python`
must all carry `help`. `go`, `kotlin` and `rust` are listed as exempt in
`_PACKS_WITHOUT_HELP` in `tests/test_templates.py` until they are authored.

### The `label:` key

`label:` is the short category name the wizard shows above a group of checkboxes —
"Everyday rules", "Common mistakes". It belongs to the pack that **owns** the
document; contributions inherit the owner's label, so a language pack does not
repeat it. Without one, the wizard falls back to the filename.

### Rule ids are namespaced

Ids become `<pack>:<local id>` — `java:no-raw-types`, `base:no-hardcoded-secrets`.
Several packs legitimately define a rule with the same local name, and without
namespacing they would collide the moment two languages are selected.

## Workflows

Workflow documents live in `base/workflows/` and declare themselves in
frontmatter. The scanner ignores the extra keys.

```yaml
---
title: Workflow - New feature - {{project}}
description: ...
id: new-feature
required: true                 # locked on; the scanner fails without it
triggers: [add feature, new feature]
---
```

The scanner hard-requires `new-feature`, `bug-fix`, `security-fix` and
`refactor`, so those must exist and be marked `required: true`.

## Validation

A pack fails to load if:

- a manifest key is missing or empty, or `kind` is not `base`/`language`
- a language pack declares no `gate`
- a rule id is duplicated within the pack
- a rule names a group the owner never declared *(checked at composition)*
- a rule has a severity outside `hard`/`soft`, or no title
- a rule's `help` is not a string
- a rule declares an `example` that is not a mapping, or that sets neither
  `bad` nor `good`
- a rule file declares no groups or no rules
- `merge` is not one of the two modes
- the same path is produced by both a rule file and a plain doc

Composition additionally fails if two selected packs own or write the same path,
or if a contribution targets a document no selected pack owns.

## Placeholders

`{{name}}` is substituted in plain docs, rule bodies, titles and intros.

Always available:

| Placeholder | Value |
| --- | --- |
| `project` | the project name |
| `languages` | e.g. `Java 21, TypeScript 5.x` |
| `gates` | one `bash gates/scripts/verify-*.sh` line per selected language |
| `language_rows` | table rows, one per language |
| `language_doc_rows` | table rows linking each language's docs |
| `workflow_rows` | the workflow trigger table for the chosen workflows |

Every other name in a pack's `placeholders` must be supplied by the caller. An
unresolved placeholder anywhere in the output aborts scaffolding, so a pack can
never write a literal `{{package}}` into somebody's standards.

## What a composition must produce

The scanner requires these in every project, so a composition that omits one
scaffolds red:

```
AGENTS.md            ARCHITECTURE.md      core/guardrails.md
core/git.md          core/definition-of-done.md    core/glossary.md
gates/README.md
workflows/new-feature.md   workflows/bug-fix.md
workflows/security-fix.md  workflows/refactor.md
```

Every markdown document **must** carry `title` and `description` frontmatter and
contain a table or fenced code block, or it scores amber once scaffolded. Quote
any frontmatter value containing `: ` — an unquoted colon silently voids the whole
frontmatter block, and a plain scalar may not start with a backtick.

The bundled packs are covered by a test that scaffolds **every combination** of
languages and asserts each result is green with nothing missing. Any new pack
should be added to it.
