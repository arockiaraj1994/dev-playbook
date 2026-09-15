# dev-playbook

This repo's `origin` remote (`git@github.com:arockiaraj1994/dev-playbook.git`) is
personal (account `arockiaraj1994`), not the work Baton Systems account.

## Git identity

`origin` is the personal repo `arockiaraj1994/dev-playbook`, and the machine's default
SSH key (`~/.ssh/id_ed25519`) authenticates to GitHub as that **personal** account
**`arockiaraj1994`** — verify with `ssh -T git@github.com` ("Hi arockiaraj1994!"; the
key's `.pub` comment is `arockiaraj1994@gmail.com`). So **pushing this repo with the
default key is correct**; it does not use a work account.

Commit identity falls back to the global git config, which is already personal, so no
repo-local `core.sshCommand` / `user.*` override is set or needed:

```
user.name  = Arockiaraj Rayappan
user.email = arockiaraj1994@gmail.com
```

The other key on this machine, `~/.ssh/id_ed25519_praximind`, is a different account
(`praximind@gmail.com`) — don't use it for this repo.

> Corrected 2026-09-15: an earlier setup kept the personal key in `~/.ssh/id_aroc` and
> treated the default `~/.ssh/id_ed25519` as the work account. That key is gone and the
> default key is now the personal account, so the old "don't push with the default key"
> rule no longer applies.
