# ansible-role: lsp

Installs **language servers** on a dev host — **pyright** and **ruff** for Python,
**rust-analyzer** with a matching toolchain for Rust — and proves each one works by speaking LSP
to it.

Everything a language-server client in this repo does starts the same way: it runs a command
**by name from `PATH`**. Neovim, Emacs/Eglot, Doom, OpenCode and Claude Code all look for
`pyright-langserver` and `rust-analyzer`. So this role installs system-wide and links the
commands into `/usr/local/bin`, and one install serves every editor, every user and the
[`paseo`](../paseo/README.md) daemon's agents. It has no per-user state and depends on no other
role.

**Everything is opt-in.** With the defaults the role does nothing, so it is safe to keep listed
in a fleet play and switch on per host or group.

**Platforms:** Gentoo, Debian/Ubuntu, Fedora/RHEL, Arch — the same four families as `paseo`.

## Quickstart

```yaml
# inventory.yml — per host or group
lsp_install_python: true
lsp_install_rust: true
```

On Fedora, Arch and Gentoo that is all; Debian and Ubuntu need a version pinned for what they
don't package (see below). The repo-root `site.yml` already lists this role. Try it on one host
first, then ask the servers for real answers:

```sh
ansible-playbook -i inventory.yml test-lsp.yml --limit <host> --check
ansible-playbook -i inventory.yml test-lsp.yml --limit <host>
ansible-playbook -i inventory.yml test-lsp-verify.yml --limit <host>
```

Needs the `community.general` collection, like the other roles here
(`ansible-galaxy collection install -r paseo/requirements.yml`).

## Python

`lsp_python_servers` defaults to both, because they do different jobs and editors run them side
by side:

| Server | Command | What it is for | Installed from |
|---|---|---|---|
| **pyright** | `pyright-langserver --stdio` | types, completion, go-to-definition | npm, into `/opt/lsp-npm` |
| **ruff** | `ruff server` | lint and format | the distro, or the official binary |

### pyright

npm is pyright's only official channel, so that is what the role uses — into a prefix of its own
(`lsp_npm_prefix`), never the system one. It runs on Node: with the `paseo` role in the play Node
is already there, and standalone the role installs the distro's `nodejs` + `npm` if `npm` is
missing (`lsp_manage_node`).

| `lsp_pyright_version` | What you get |
|---|---|
| `""` (default) | The newest version at *first* install, then left alone. |
| `"1.1.414"` | Exactly that version; move the pin to upgrade or downgrade. |

**basedpyright** — the community fork — is one variable away:

```yaml
lsp_pyright_package: basedpyright
```

Its npm package installs `pyright-langserver` *as well as* `basedpyright-langserver`, so every
client that asks for the former keeps working. The two packages cannot share a prefix (same
command names), so switching removes the other one.

### ruff

| `lsp_ruff_version` | What you get |
|---|---|
| `""` (default) | The distro package: Fedora, Arch, Gentoo. **Debian and Ubuntu have none** — the play stops and says so. |
| `"0.17.6"` | ruff's official release binary in `/opt/ruff-0.17.6`, linked into `/usr/local/bin`. Same on every distro, and pinned. |

Gentoo's stable keyword trails upstream (0.16 while upstream is 0.17); pin a version there if you
want the current one. `lsp_ruff_checksum` takes the `sha256:…` digest published beside each
release asset.

## Rust

rust-analyzer is only as good as the toolchain beside it: it shells out to `cargo` and `rustc`,
and without the standard library's *source* (`rust-src`) it cannot complete or navigate anything
in `std`. So the role always installs the set — toolchain, `rust-src`, `rust-analyzer`, plus
`rustfmt` and `clippy` — **from one origin**, so the server matches the compiler it drives.

| `lsp_rust_toolchain` | What you get |
|---|---|
| `""` (default) | The distro's packages. Right for Fedora, Arch, Gentoo and Debian 13+. |
| `"1.100.0"` | The official toolchain of that version under `/opt/rust`, linked into `/usr/local/bin`. |
| `"stable"` | Same, following the channel: installed once, then moved only when you set `lsp_rust_update: true`. |

**Debian 12 and Ubuntu have no usable `rust-analyzer` package** — Debian 12 has none at all (and
ships Rust 1.63), Ubuntu 24.04 has none, and 26.04 only a versioned one that does not match its
default compiler. The distro route fails there with a message telling you to set
`lsp_rust_toolchain`.

### A fixed toolchain, on purpose

Both routes give you a *system-wide, root-owned* toolchain — every user and the daemon get the
same one, and nobody but root can change it. The official route downloads with rustup but does
**not** hand you a rustup install: rustup stays private under `/opt/rust`, and the links in
`/usr/local/bin` point at the toolchain's real binaries, not at rustup's shims.

That is deliberate. A shim only works for a user whose own `~/.rustup` holds a default toolchain,
and it answers `rust-analyzer --version` with *"Unknown binary 'rust-analyzer' in official
toolchain"* when the component isn't there — on `PATH`, and not working. A real binary finds its
sysroot from its own location and works for every account with no environment to set up.

The cost: **a project's `rust-toolchain.toml` is not honoured.** `cargo build` uses the installed
toolchain whatever the file says, exactly as with a distro package. If you need rustup's
per-project switching, install rustup for that user yourself; this role and that setup do not
conflict, and whichever comes first on that user's `PATH` wins.

### Gentoo

On Gentoo `rust-analyzer` and `rust-src` are USE flags of the Rust package itself. The role
writes them to `/etc/portage/package.use/coral-lsp-rust` and emerges with `--changed-use`:

- no Rust installed, or `dev-lang/rust-bin` → it uses `dev-lang/rust-bin`. Quick.
- a source-built `dev-lang/rust` → it extends *that* package, which **recompiles the compiler**.

To avoid the rebuild, set `lsp_rust_gentoo_atom: dev-lang/rust-bin`, or use the official route
(`lsp_rust_toolchain`), which leaves portage's Rust alone.

## Verification

"The binary is on `PATH`" proves very little, so the role does not stop there. After installing,
it starts each server and completes a real LSP `initialize` handshake
([`files/lsp-probe.py`](files/lsp-probe.py), stdlib-only Python) — **as `nobody`, not root**, so an
install that only root can read fails in the play instead of in your editor. That one exchange
catches a rustup shim with no component behind it, pyright without Node, a binary built for a
newer glibc, and a tree with the wrong permissions. For Rust it also checks that `rust-src` is
really in the sysroot.

`test-lsp-verify.yml` goes further and asks for what an editor would show you: a type error from
pyright, a lint finding from ruff, a type error from rust-analyzer via `cargo check`, and
go-to-definition landing inside typeshed and inside the standard library's source. It leaves the
host unchanged.

`lsp_verify: false` turns the in-role check off; `lsp_verify_user` picks another account.

## Using the servers

The role installs servers; it does not edit anyone's editor config. This is what each client
needs.

### Neovim

**Neovim 0.11+, no plugins** — the built-in client is enough:

```lua
-- ~/.config/nvim/init.lua
vim.lsp.config('pyright', {
  cmd = { 'pyright-langserver', '--stdio' },
  filetypes = { 'python' },
  root_markers = { 'pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'pyrightconfig.json', '.git' },
})
vim.lsp.config('ruff', {
  cmd = { 'ruff', 'server' },
  filetypes = { 'python' },
  root_markers = { 'pyproject.toml', 'ruff.toml', '.ruff.toml', '.git' },
})
vim.lsp.config('rust_analyzer', {
  cmd = { 'rust-analyzer' },
  filetypes = { 'rust' },
  root_markers = { 'Cargo.toml', '.git' },
})
vim.lsp.enable({ 'pyright', 'ruff', 'rust_analyzer' })
```

With [nvim-lspconfig](https://github.com/neovim/nvim-lspconfig) installed the three `config`
blocks are already provided and only the last line is needed.

**LazyVim** — enable the two language extras, and tell it not to download its own copies with
Mason. Its Rust extra *requires* `rust-analyzer` on `PATH` and never installs it, so that half
needs nothing:

```lua
-- lua/plugins/languages.lua
return {
  { import = "lazyvim.plugins.extras.lang.python" },
  { import = "lazyvim.plugins.extras.lang.rust" },
  {
    "neovim/nvim-lspconfig",
    opts = { servers = { pyright = { mason = false }, ruff = { mason = false } } },
  },
}
```

The `mason = false` lines are what make LazyVim use the copies on `PATH`. Without them it hands
those two servers to Mason and starts neither until Mason has installed its own — the servers this
role put there are ignored.

### Emacs

**Eglot** (built into Emacs 29+) already knows both commands — `M-x eglot` in a Python or Rust
buffer, or:

```elisp
(add-hook 'python-mode-hook #'eglot-ensure)
(add-hook 'rust-ts-mode-hook #'eglot-ensure)
```

Eglot runs one server per buffer and, for Python, takes the first of its candidates it finds on
`PATH`; `pyright-langserver` is ahead of `ruff` in that list, so you get pyright. Stock Emacs has
no Rust major mode until you add one: `rust-ts-mode` needs its tree-sitter grammar, or install the
`rust-mode` package.

**Doom Emacs** — in `~/.config/doom/init.el`, then `doom sync`:

```elisp
:tools
lsp                      ; or (lsp +eglot)
:lang
(python +lsp +pyright)
(rust +lsp)
```

With the default `lsp` (lsp-mode) a Python buffer gets pyright *and* ruff side by side, and a Rust
buffer gets rust-analyzer — all three found on `PATH`, nothing downloaded.

### OpenCode

OpenCode 1.x has both servers built in and looks for `pyright-langserver` and `rust-analyzer` on
`PATH` — but **LSP is off unless the config enables it**. In `opencode.jsonc`:

```jsonc
{
  "lsp": true
}
```

Two things worth knowing before you do. OpenCode's own docs call this a trade-off — language
servers use memory and can slow an agent down, and running the linter or `cargo check` directly
is often the better feedback loop. And **OpenCode 2 does not run language servers at all**; it
accepts the key and ignores it. The `paseo` role installs the 1.x line.

Without a `pyright-langserver` on `PATH`, OpenCode 1.x downloads its own at first use. With this
role's install in place it uses that one instead, so the version is the one in your inventory.
`rust-analyzer` it never downloads: no binary on `PATH`, no Rust diagnostics.

### Claude Code

Claude Code gets language servers through plugins, and the two official ones run exactly these
commands: `pyright-lsp` (`pyright-langserver --stdio`) and `rust-analyzer-lsp` (`rust-analyzer`).
Declare them in the `paseo` role, which owns the daemon user's `~/.claude/settings.json`:

```yaml
paseo_claude_settings_extra:
  env:
    CLAUDE_CODE_SYNC_PLUGIN_INSTALL: "1"
  enabledPlugins:
    "pyright-lsp@claude-plugins-official": true
    "rust-analyzer-lsp@claude-plugins-official": true
```

That is the whole setup. Claude Code registers the official marketplace and downloads both
plugins itself the first time a session starts (it clones from GitHub, so the host needs `git`),
and loads them from its cache after that.

- **The `env` line matters.** Without it a headless session does not wait for the download, and
  in testing nothing was installed at all. With it the first start took about a second longer and
  later starts cost nothing.
- **Don't use `claude plugin install` for the daemon user.** It works, but it rewrites
  `settings.json`, the role writes its own version back on the next play, and that restarts the
  daemon. Declared plugins also never show up in `claude plugin list`, which only knows about
  CLI installs; the `init` event of `claude -p … --output-format stream-json --verbose` lists
  what a session really loaded.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `lsp_install_python` | `false` | Install the Python servers. |
| `lsp_python_servers` | `[pyright, ruff]` | Which ones. |
| `lsp_pyright_package` | `pyright` | Or `basedpyright`. |
| `lsp_pyright_version` | `""` | Empty = newest at first install; a version = exactly that. |
| `lsp_manage_node` | `true` | Install the distro's Node when `npm` is missing. |
| `lsp_ruff_version` | `""` | Empty = distro package; a version = official binary. |
| `lsp_ruff_checksum` | `""` | Optional `sha256:…` for the ruff tarball. |
| `lsp_install_rust` | `false` | Install rust-analyzer + toolchain. |
| `lsp_rust_toolchain` | `""` | Empty = distro packages; a version or channel = official toolchain. |
| `lsp_rust_components` | `[rust-analyzer, rust-src, clippy, rustfmt]` | Official route: what the toolchain carries. |
| `lsp_rust_update` | `false` | Official route, channels only: update on every play. |
| `lsp_rust_gentoo_atom` | `""` | Gentoo, distro route: which Rust package carries the USE flags. |
| `lsp_skip_if_present` | `true` | Leave an already-working server alone (unpinned routes only). |
| `lsp_verify` | `true` | Handshake with each server after installing. |
| `lsp_verify_user` | `nobody` | The account the handshake runs as. |
| `lsp_bin_dir` | `/usr/local/bin` | Where commands are linked. |
| `lsp_install_root` | `/opt` | Where pinned builds live. |

Full list with comments in `defaults/main.yml`.

## Notes

- **A pin wins, and nothing is removed.** `/usr/local/bin` precedes `/usr/bin`, so a pinned ruff
  or toolchain takes over from a distro copy without uninstalling it. Older pinned versions stay
  under `/opt`; point the link back to roll back.
- **`lsp_skip_if_present`** means *works*, not *exists*: the role runs `rust-analyzer --version`
  and `ruff --version` rather than looking for a file. For Rust it skips only a complete setup —
  a host with `rust` and `cargo` but no `rust-analyzer` gets the missing packages added.
- **Arch's `rustup` package** owns `/usr/bin/cargo` and `/usr/bin/rust-analyzer` and conflicts
  with the `rust` package. On a host that has it, use `lsp_rust_toolchain`.
- **RHEL-likes** get `ruff` from EPEL; enable it first.
- **Other architectures and musl.** The official downloads default to
  `<arch>-unknown-linux-gnu` for x86_64 and aarch64. Set `lsp_rust_target` / `lsp_ruff_target`
  for anything else.
- **Root's umask.** npm and rustup run under `umask 022` regardless of the host's, so a hardened
  root umask cannot leave trees that only root can read.

## What's verified

Run for real in containers — not just syntax-checked. Each install below was run **twice**, the
second run reporting no changes, and then put through `test-lsp-verify.yml` with the servers
running as `nobody`: a type error and a typeshed definition from pyright, an unused import from
ruff, and from rust-analyzer a type error (via `cargo check`) plus a definition inside the
standard library's source.

**Installs**

- **Fedora 44** (systemd; alone, and in one play after `paseo` and `editors`) — Rust and ruff from
  the distro, pyright from npm.
- **Arch** — the same; and a host that already had Arch's own `pyright` package and only `rust`:
  the existing pyright was left alone, the missing `rust-src` and `rust-analyzer` were added, and
  a later `lsp_pyright_version` pin took over from the distro copy without removing it.
- **Debian 13** — distro Rust (1.85) with a pinned ruff. Unpinned, the play stops at "Debian has no
  ruff package".
- **Debian 12** — the distro route stops with the message pointing at `lsp_rust_toolchain`; then
  the official toolchain 1.100.0 with a pinned ruff. Also there: the `stable` channel with
  `lsp_rust_update`, a component added later, and the pin moved to 1.99.0 (the links follow).
- **Gentoo** (real emerges; Node and ruff as binary packages from Gentoo's binhost) —
  `dev-lang/rust-bin` with the USE flags this role writes, both from nothing and on top of an
  existing rust-bin that lacked them; the stable `dev-util/ruff`, then a pinned one taking over.
- **A host where every process runs with umask 077** — everything still usable by `nobody`.
- **Changes of mind** — a pyright pin moved down and back, `basedpyright` swapped in and out
  again (its two extra links are removed).
- **`--check`** — on a pristine host by both routes, and on provisioned hosts (no changes).
- **Failures on purpose** — a tree only root can read, and a rustup-style shim ahead of the real
  `rust-analyzer`: both stop the play with a message that names the cause.

**Clients**, each on a host provisioned by this role:

- **Neovim** 0.11.6 (Fedora) and 0.12.6 (Arch), with exactly the plugin-free snippet above:
  pyright and ruff attach to a Python buffer, rust-analyzer to a Rust one, diagnostics arrive.
- **LazyVim** (Arch) with the recipe above: the same three, from `PATH`. Without the
  `mason = false` lines nothing attached to the Python buffer.
- **Eglot**, Emacs 30.2 (Fedora): connects to `pyright-langserver` and `rust-analyzer` and gets
  hover text for `os.path.join` and `Vec` back.
- **Doom Emacs** for the daemon user (Fedora, `lsp` + `(python +lsp +pyright)` + `(rust +lsp)`),
  in a live session: pyright, ruff and rust-analyzer all initialized, with their diagnostics.
- **OpenCode** 1.19.6, run as the daemon user with the daemon's own `PATH`: with `"lsp": true` it
  returns pyright's diagnostics; without it, nothing. It starts `rust-analyzer` from `PATH` and
  completes the handshake, but its one-shot `debug lsp diagnostics` command returns before
  rust-analyzer has loaded the project, so no Rust diagnostics were seen through OpenCode.
- **Claude Code** 2.1.325, headless as the daemon user: with the settings above it registered the
  marketplace, downloaded both plugins and listed them — and its `LSP` tool — in the session.

**Not run:** SELinux enforcing (the containers had it off; nothing here is labelled specially);
RHEL-likes and EPEL; Ubuntu beyond checking what it packages; aarch64 and musl hosts; Gentoo with
a source-built `dev-lang/rust` (the compiler rebuild); and an authenticated agent session in which
OpenCode or Claude Code actually *uses* a language server — there were no credentials in the test
containers, so the agent checks stop at "the server is found, started and answering".
