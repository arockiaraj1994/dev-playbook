# dev-agent-playbook

This repo's `origin` remote (`git@github.com:arockiaraj1994/dev-agent-playbook.git`) is
personal (account `arockiaraj1994`), not the work Baton Systems account.

## Git identity

The machine's default SSH key (`~/.ssh/id_ed25519`) authenticates as the **work** GitHub
account. To make every commit/push/fetch in this repo use the personal account instead,
these are set in this repo's **local** git config (not global — other repos are unaffected):

```
user.name       = Arockiaraj Rayappan
user.email      = arockiaraj1994@gmail.com
core.sshCommand = ssh -i ~/.ssh/id_aroc -o IdentitiesOnly=yes
```

`~/.ssh/id_aroc` is the personal key (public key comment: `arockiaraj1994@gmail.com`).
Do not change these to the work identity/key, and do not push using the default SSH key
from this repo.
