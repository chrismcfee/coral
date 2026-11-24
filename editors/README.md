# ansible-role: editors

Installs **Neovim** and/or **Emacs** on a dev host and, optionally, deploys their configuration
for one user — including a full **Doom Emacs** install and a headless plugin bootstrap for Neovim.

It pairs with the [`paseo`](../paseo/README.md) role: used in the same play it configures the
editors for that role's daemon user, i.e. the account your agents and Paseo terminals run as. It
has no dependency on it, though, and works standalone.

**Everything is opt-in.** With the defaults the role does nothing at all, so it is safe to keep
listed in a fleet play and switch on per host or group.

**Platforms:** Gentoo, Debian/Ubuntu, Fedora/RHEL, Arch — the same four families as `paseo`.

## Quickstart

```yaml
# inventory.yml — per host or group
editors_install_neovim: true
editors_install_emacs: true
```

That installs the editors plus `git`, `ripgrep` and `fd` from the distro. Nothing per-user yet.
The repo-root `site.yml` already lists this role after `paseo`.

Try it on one host first:

```sh
ansible-playbook -i inventory.yml test-editors.yml --limit <host> --check
ansible-playbook -i inventory.yml test-editors.yml --limit <host>
```

## Neovim

### Which Neovim

| `editors_neovim_version` | What you get |
|---|---|
| `""` (default) | The distro package. Current on Gentoo, Fedora and Arch. |
| `"0.12.6"` | Neovim's **official release tarball**, unpacked to `/opt/nvim-0.12.6` and symlinked into `/usr/local/bin`. Identical on every distro, and pinned. |

Use the pinned build on **Debian/Ubuntu**: Debian 12 ships Neovim 0.7.2, far too old for current
configs (LazyVim needs ≥ 0.11.2). `/usr/local/bin` precedes `/usr/bin`, so the pinned build wins
without removing the distro one. To verify the download, set `editors_neovim_checksum` to the
`sha256:…` digest published on the GitHub release.

`editors_neovim_min_version` fails the play with a clear message when the resulting `nvim` is
older than your config needs — the same idea as the Node version gate in `paseo`.

### Configuration

Pick **one** source:

```yaml
# a directory next to your playbooks, copied to ~/.config/nvim
editors_neovim_config_src: "{{ playbook_dir }}/nvim"

# …or a git repository cloned on the target
editors_neovim_config_repo: "https://github.com/LazyVim/starter"
editors_neovim_min_version: "0.11.2"
editors_extra_packages: [gcc]          # nvim-treesitter needs a C compiler; name is per distro
```

After the config lands or changes, the role installs its plugins headlessly
(`nvim --headless "+Lazy! install" +qa`), so the first interactive start isn't a multi-minute
download. That default suits [lazy.nvim](https://github.com/folke/lazy.nvim)-based configs —
LazyVim, kickstart.nvim and most current setups. `install` only fetches what is *missing*, at the
commits in `lazy-lock.json` when your config ships one, and never updates what is already there.
**Commit your `lazy-lock.json`** and every host gets the same plugin versions.

For a config that isn't lazy.nvim based, replace `editors_neovim_bootstrap_command` or set
`editors_neovim_bootstrap: false`.

Things the headless pass does *not* do: Mason tool installs and Treesitter parser builds that a
config triggers asynchronously still happen on first interactive use.

## Emacs

`editors_install_emacs: true` installs the headless build (`emacs-nox`; `app-editors/emacs` on
Gentoo). For a GUI build on a workstation, override the package:

```yaml
editors_emacs_packages: [emacs-wayland]
```

### Doom Emacs

```yaml
editors_install_emacs: true
editors_emacs_doom: true
editors_doom_version: "8b9b1ea14d02c6f18114a8e183469f685e0fa34e"   # pin a commit for a fleet
```

This clones Doom into `~/.config/emacs` and runs `doom --force install --no-env` for the user.
**It is slow the first time** — it downloads and byte-compiles a hundred-odd packages, which
takes minutes and a few hundred MB under the user's home.

Written against Doom's *current* sources, which differ from most write-ups:

- The repository is **`doomemacs/core`** (`doomemacs/doomemacs` was renamed), and Doom's modules
  are git submodules that `doom install` initializes itself.
- Doom needs **Emacs ≥ 29.1** as a starter kit (27.1 only runs its CLI). The role asserts this, so
  a Debian 12 host (Emacs 28.2) fails with a clear message instead of a broken install.
- `--no-env` is deliberate: the "envvar file" Doom offers to generate is a snapshot of the invoking
  shell's environment, which under Ansible is sudo's.

Your private config (`init.el` / `config.el` / `packages.el`) takes one source, like Neovim's:

```yaml
editors_doom_config_src: "{{ playbook_dir }}/doom"      # a directory on the controller
# editors_doom_config_repo: "https://github.com/you/doom-config"
```

With neither set, `doom install` generates Doom's defaults in `~/.config/doom`.

**Emacs prefers `~/.emacs`, `~/.emacs.el` and `~/.emacs.d` over `~/.config/emacs`.** If one of
those exists for the user, Doom installs correctly and is then never loaded. The role warns about
this and does not delete anything for you.

## Pinning and updates

The same rule applies to Doom and to every git-sourced config:

| `*_version` | Behaviour |
|---|---|
| a branch or tag (`master`, `HEAD`) | Cloned once, then left alone. Set `*_update: true` to follow it on every play. |
| a full 40-character commit SHA | A **pin**. Every play converges the host to it. |

So "what version is this host on" is answerable from your inventory once you pin, and an unpinned
host never changes underneath you. After a pin moves or a config changes, the role re-runs the
relevant bootstrap (`doom install`, or the Neovim plugin install).

Bootstraps are tracked with marker files under `~/.local/state/coral-editors/`. The marker is
removed *before* a re-run and written only after success, so a bootstrap that dies on a flaky
network is retried on the next play rather than trusted.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `editors_user` / `editors_home` | paseo role's user / home | Who gets the config. Required only when a config or Doom is deployed. |
| `editors_install_neovim` | `false` | Install Neovim. |
| `editors_neovim_version` | `""` | Empty = distro package; a version = official tarball. |
| `editors_neovim_checksum` | `""` | Optional `sha256:…` for the tarball. |
| `editors_neovim_min_version` | `""` | Fail if `nvim` is older. |
| `editors_neovim_config_src` / `_repo` | `""` | One config source: controller dir or git URL. |
| `editors_neovim_bootstrap` | `true` | Headless plugin install after the config changes. |
| `editors_install_emacs` | `false` | Install Emacs (headless build). |
| `editors_emacs_doom` | `false` | Install Doom Emacs for `editors_user`. |
| `editors_doom_version` | `master` | Branch, or a commit SHA to pin. |
| `editors_doom_config_src` / `_repo` | `""` | Your private Doom config. |
| `editors_install_support_tools` | `true` | `git`, `ripgrep`, `fd`. |
| `editors_extra_packages` | `[]` | Extra distro packages, named for the host's package manager. |
| `editors_skip_if_present` | `true` | Don't install a package for an editor already on `PATH`. |
| `editors_bootstrap_timeout` | `3600` | Seconds allowed for one plugin/package bootstrap. |

Full list with comments in `defaults/main.yml`.

## Notes

- **`editors_skip_if_present`.** If `nvim`/`emacs` is already on `PATH`, the role leaves the
  packages alone. That is what keeps it from fighting an install it didn't make: Arch's `emacs-nox`
  conflicts with an installed `emacs`, and on Gentoo it avoids rebuilding Emacs for nothing.
- **RHEL-likes** get `neovim`, `ripgrep` and `fd-find` from EPEL; enable it first. Fedora needs
  nothing extra.
- **Debian's `fd`** installs as `fdfind`. Doom and the common Neovim pickers look for both names.
- **Controller-directory configs are additive.** A file you delete from the source is not removed
  from the target.
- **Language servers** are a separate role: [`lsp`](../lsp/README.md) installs pyright, ruff and
  rust-analyzer system-wide, and its README has the lines Neovim, LazyVim, Eglot and Doom need to
  use them.
- **With a hardened `paseo` unit** (`ProtectSystem=strict`), editors launched *inside* the daemon —
  a Paseo terminal, an agent shelling out — can only write under the daemon's home. Everything this
  role sets up lives there, so `doom sync` and plugin updates work from a Paseo terminal.

## What's verified

Run for real in containers — not just syntax-checked. Each install below was run **twice**, the
second run reporting no changes:

- **Debian 12** — distro packages; the version gate tripping on the distro's Neovim 0.7.2; the
  official 0.12.6 tarball with its published checksum; a wrong checksum failing before anything is
  unpacked; Doom refused because Emacs is 28.2.
- **Fedora 44** (systemd, in one play with the `paseo` role, user and home taken from it) — Neovim
  with a config from a controller directory and its plugins installed; Doom pinned to a commit,
  then loaded in a live Emacs session for the daemon user (33 modules). Editing the Neovim config
  re-ran only the Neovim bootstrap.
- **Arch** (standalone, explicit user) — the LazyVim starter from git with all 32 of its plugins
  installed; Doom on a branch with a private config from a controller directory.
- **`--check`** — a dry run on a fresh host and on a provisioned one both complete.
- **Gentoo** — the package atoms resolve against the current tree with `emerge --pretend`
  (`app-editors/neovim`, `app-editors/emacs`, `sys-apps/ripgrep`, `sys-apps/fd`, `dev-vcs/git`).

Not run: an actual `emerge` on Gentoo, RHEL-likes with EPEL, a GUI Emacs build, and a host with
SELinux enforcing.
