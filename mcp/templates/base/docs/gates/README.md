---
title: Gates - {{project}}
description: Executable verification scripts that must pass before a task is complete.
tags: [gates]
---

# Gates - {{project}}

Run every gate for the languages this change touched, from the project root:

```bash
{{gates}}
```

| When | Why |
| --- | --- |
| Before claiming a task complete | The definition of done requires it |
| After adding or upgrading a dependency | Catches version and licence drift |
| After changing build configuration | The build is part of the product |

Each script exits non-zero on the first failing step. Adjust the commands inside
to match this project's build tooling - the templates ship the common case.
