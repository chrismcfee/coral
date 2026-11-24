# README

Opinionated reproducible dev environments extendable via ansible playbooks

(Original name was opencode-paseo installer)

adding support in for editors such as neovim, lsp servers, and alternatives for all kinds of dev environments to make reproducible dev environments a little less manual.

**What's here now**

| Piece | Where | Status |
|---|---|---|
| Paseo daemon fleet (systemd / OpenRC, NetBird mesh) | [`paseo/`](paseo/README.md) | verified against `@getpaseo/cli` 0.17.4 |
| OpenCode agent | `paseo/` | on by default |
| Claude Code agent | `paseo/` | opt-in (`paseo_install_claude`) |
| Kimi Code agent (over ACP) | `paseo/` | opt-in (`paseo_install_kimicode`) |
| Neovim, Emacs, Doom Emacs | [`editors/`](editors/README.md) | opt-in (`editors_install_neovim`, `editors_install_emacs`, `editors_emacs_doom`) |
| Language servers: Python (pyright, ruff) and Rust (rust-analyzer + toolchain) | [`lsp/`](lsp/README.md) | opt-in (`lsp_install_python`, `lsp_install_rust`) |

Not built yet: Codex (design in `docs/superpowers/specs/`), language servers beyond Python and Rust.

## Old readme:

#### oc-paseo-setup-installer

Ansible automation that stands up a fleet of [**Paseo**](https://getpaseo.com) daemons and the
[**OpenCode**](https://opencode.ai) CLI they drive — installed, configured, secured, and reachable
remotely over a [NetBird](https://netbird.io) mesh — on **systemd _or_ OpenRC**, across **Gentoo,
Debian/Ubuntu, Fedora/RHEL, and Arch** (all auto-detected per host).

One daemon per box, each binding its own mesh IP. The role templates **config, never state**: it
manages `config.json` and the service unit but never touches a daemon's keypair, so the same play
runs across the whole fleet without two daemons ever sharing an identity.

> **Verified, not guessed.** The Paseo CLI (`@getpaseo/cli`) was installed and inspected to pin the
> exact daemon flags, the `config.json` schema, and the password hashing. The generated `config.json`
> and bcrypt password were tested against the **real daemon** (health check + live auth). See
> [What's verified](#whats-verified).

---

############ What it does

1. Creates a hardened system `paseo` user (home under `/var/lib/paseo`).
2. Installs Node (≥22, version-gated) and the Paseo CLI into a per-user npm prefix.
3. **Downloads + installs the OpenCode CLI** (official installer) for that user, and **deploys your
   sanitized `opencode.jsonc`** to its config dir.
4. **Injects your OpenCode provider secrets** from Ansible Vault into the daemon's environment — one
   file loaded by **both** systemd and OpenRC — so `{env:VAR}` resolves when Paseo launches OpenCode.
5. Writes the daemon's bcrypt password directly into `config.json` (rotation-safe, idempotent).
6. Templates `config.json` to bind the mesh IP with a Host-header allowlist.
7. Drops a **systemd unit** or **OpenRC service** (auto-detected) and enables + starts it.

## How it works

```
                        ┌──────────────── your controller (workstation) ────────────────┐
   ~/.config/opencode/.env   ──►  group_vars/paseo_fleet/vault.yml   ──►  ansible-playbook
   (real secrets, never        (ansible-vault encrypted, gitignored)     site.yml
    committed)                  built by make-vault.sh                    (vault-pass.sh
                                                                           resolves the pw)
                        └───────────────────────────────┬───────────────────────────────┘
                                                         │  over SSH, become: true
                                                         ▼
                        ┌──────────────────────── each target box ──────────────────────┐
   opencode.jsonc  ──►  ~/.config/opencode/opencode.jsonc      (deployed verbatim)
   ({env:VAR})          /etc/paseo/paseo.env  0640 root:paseo  (secret VALUES land here)
                        config.json  daemon.{listen,hostnames,auth.password,relay}
                        systemd paseo.service  /  OpenRC /etc/init.d/paseo
                                     │  binds 100.x:6767 on the NetBird mesh
                                     ▼
                        paseo daemon  ──launches──►  opencode  (inherits the env → {env:VAR} resolves)
                        └───────────────────────────────────────────────────────────────┘
```

############# Repository layout

```
.
├── README.md                      ← you are here (project overview + usage)
├── site.yml                       ← main playbook: run from repo root
├── inventory.yml                  ← your fleet: one host per box + its mesh IP
├── ansible.cfg                    ← roles_path, inventory, vault-password resolver
├── opencode.jsonc                 ← your SANITIZED OpenCode config (secrets are {env:VAR})
├── group_vars/paseo_fleet/
│   └── vault.yml.example          ← template for the encrypted secrets (copy → vault.yml)
│
├── make-vault.sh                  ← build the encrypted vault from your .env, one shot
├── opencode-secrets-from-env.sh   ← .env  →  paseo_opencode_secrets: YAML block
├── vault-pass.sh                  ← resolves the vault password (env / file / keyring / prompt)
├── redact-json.py                 ← strip secrets from a JSON config for safe export
├── opencode-effective-config.py   ← print OpenCode's merged effective config
│
├── paseo/                         ← the Ansible role (homelab.paseo)
│   ├── README.md                  ← role reference: full variable list + internals
│   ├── defaults/main.yml          ← every tunable, commented
│   ├── tasks/                     ← install, opencode/claude/kimicode install, password, config, …
│   ├── templates/                 ← paseo.service.j2, paseo.openrc.j2, paseo.conf.d.j2, paseo.env.j2
│   ├── files/                     ← resolve-opencode-config.sh + helper scripts
│   └── example/                   ← library-style example (drive the role directly)
│
├── editors/                       ← second role (homelab.editors): Neovim / Emacs / Doom Emacs
│   ├── README.md                  ← role reference
│   ├── defaults/main.yml          ← every tunable, commented
│   └── tasks/                     ← neovim, emacs, doom, shared package + config-source tasks
│
├── lsp/                           ← third role (homelab.lsp): language servers for Python and Rust
│   ├── README.md                  ← role reference, incl. how each editor and agent picks them up
│   ├── defaults/main.yml          ← every tunable, commented
│   ├── tasks/                     ← python, rust (distro + official toolchain), verify
│   └── files/lsp-probe.py         ← the LSP client that proves a server works
│
├── test-*.yml / dev-*.yml         ← single-host smoke tests (test-claude, test-kimicode, test-editors, test-lsp, …)
│
└── ansible-role-paseo.tar.gz      ← packaged snapshot of paseo/
```

## #Requirements

**Controller (your workstation):**
- Ansible ≥ 2.14 and the `community.general` collection:
  ```bash
  ansible-galaxy collection install -r paseo/requirements.yml
  ```
- `ansible-vault` (ships with Ansible). `secret-tool` (libsecret) optional, for keyring-backed vault.

**Targets (each box):**
- SSH access + `sudo` (the play uses `become: true`).
- **Node ≥ 22** — installed by the role on Gentoo/Debian/Fedora/Arch, or provide your own.
- Outbound HTTPS to npm + `github.com` (OpenCode's installer pulls a release binary).
- **NetBird** installed and up (the role only *orders after* it; it does not manage NetBird).

## Quickstart

Run everything from the repo root.

```bash
# 1. Collection
ansible-galaxy collection install -r paseo/requirements.yml

# 2. Your OpenCode config already lives at ./opencode.jsonc (secrets are {env:VAR} placeholders).
#    Edit inventory.yml with your real hosts + NetBird mesh IPs.

# 3. Choose your vault password ONCE — vault-pass.sh resolves it for every run (no --ask-vault-pass).
#    Keyring (recommended), or an env var / a ./.vault_pass file:
secret-tool store --label="paseo ansible vault" service ansible-vault key paseo

# 4. Build the encrypted vault from your OpenCode secrets .env (values never hit your terminal):
./make-vault.sh                       # reads ~/.config/opencode/.env → group_vars/paseo_fleet/vault.yml

# 5. Go (systemd or OpenRC, auto-detected per host):
ansible-playbook site.yml --ask-become-pass
```

That's it — each box ends up with a running Paseo daemon bound to its mesh IP, with OpenCode installed
and wired to your providers.

## Configuration

### `opencode.jsonc` (the config you deploy)
Your real OpenCode config with **every secret replaced by `{env:VAR}`**. It is committed (no secrets
in it); the matching values live only in the vault. Update it any time and re-run the play. If you
introduce a new `{env:NEW_VAR}`, add `NEW_VAR` to your `.env` and re-run `make-vault.sh`.

### `inventory.yml` (your fleet)
One entry per box, each binding its own mesh IP:
```yaml
all:
  children:
    paseo_fleet:
      hosts:
        box-a:
          ansible_host: 100.64.0.1
          paseo_listen: "100.64.0.1:6767"
          paseo_hostnames: ["box-a"]
```

### Key role variables
Set on the play, in `inventory.yml`, or per host. Full list in [`paseo/README.md`](paseo/README.md).

| Variable | Default | Notes |
|---|---|---|
| `paseo_listen` | `127.0.0.1:6767` | **Override per host** to the mesh IP. |
| `paseo_set_password` | `true` | `false` = rely on the mesh as the sole boundary. |
| `paseo_install_opencode` | `true` | Download + install OpenCode for the daemon user. |
| `paseo_opencode_version` | `""` | Pin OpenCode; empty = latest at first install. |
| `paseo_deploy_opencode_config` | `true` | Deploy your sanitized `opencode.jsonc`. |
| `paseo_opencode_secrets` | `{}` | `{env:VAR}` → value map, from Vault. |
| `paseo_opencode_auth` | `{}` | Optional `auth.json` (Vault) for `opencode auth login` providers. |
| `paseo_install_claude` | `false` | Opt-in: install Claude Code and register it as a Paseo provider. |
| `paseo_claude_version` | `stable` | `stable` / `latest` / exact semver. Pin exactly for a fleet. |
| `paseo_claude_secrets` | `{}` | `ANTHROPIC_API_KEY` etc., from Vault. Same env file as OpenCode's. |
| `paseo_install_kimicode` | `false` | Opt-in: install Kimi Code and register it as a Paseo (ACP) provider. |
| `paseo_kimicode_version` | `""` | Pin Kimi Code; empty = latest at first install. Pin exactly for a fleet. |
| `paseo_kimicode_secrets` | `{}` | The `KIMI_MODEL_*` family, from Vault. Kimi ignores a bare `KIMI_API_KEY`. |
| `paseo_cli_version` | `""` | Pin the Paseo CLI. Empty upgrades it on **every play**. |
| `paseo_relay_enabled` | `false` | Paseo's hosted relay; off for mesh-only fleets. |
| `paseo_manage_node` | `true` | `false` if you provide Node ≥22 yourself. |

### Running Claude Code instead of (or alongside) OpenCode

`claude` is a builtin Paseo provider, so this is an install-and-register job, not an integration:

```yaml
paseo_install_claude: true
paseo_claude_version: "2.1.325"
paseo_claude_secrets:
  ANTHROPIC_API_KEY: "{{ vault_anthropic_api_key }}"
```

All agents can be installed at once — each gets its own bin dir on the daemon's PATH, and their
secrets are merged into the one `/etc/paseo/paseo.env`. Details, including why PATH is the only
discovery lever that matters and what "available" does and doesn't guarantee, are in
[`paseo/README.md`](paseo/README.md#claude-code-as-a-second-agent).

### Running Kimi Code

`kimi` is *not* a Paseo builtin — it runs over ACP and has to be declared as a custom provider,
which the role does:

```yaml
paseo_install_kimicode: true
paseo_kimicode_version: "2.7.3"
paseo_kimicode_secrets:
  KIMI_MODEL_NAME: "kimi-for-coding"
  KIMI_MODEL_API_KEY: "{{ vault_kimi_api_key }}"
  KIMI_MODEL_BASE_URL: "https://api.kimi.com/coding/v1"
```

**Kimi Code does not read a bare `KIMI_API_KEY` from the environment** — `KIMI_MODEL_NAME` is the
switch that makes it build a provider from `KIMI_MODEL_*`. A `config.toml` provider with
`api_key_env`, or a one-off `kimi login` (OAuth) per host, work too. See
[`paseo/README.md`](paseo/README.md#kimi-code-as-a-third-agent).

### Editors

Neovim and Emacs (optionally Doom Emacs) come from a second role, [`editors/`](editors/README.md),
already listed in `site.yml`. It does nothing until you switch something on:

```yaml
editors_install_neovim: true
editors_neovim_config_repo: "https://github.com/LazyVim/starter"   # or a directory in this repo
editors_install_emacs: true
editors_emacs_doom: true
```

It configures the editors for the Paseo daemon user by default — the account your agents and Paseo
terminals run as.

### Language servers

Python and Rust language servers come from a third role, [`lsp/`](lsp/README.md), also listed in
`site.yml` and also off until you ask:

```yaml
lsp_install_python: true      # pyright + ruff
lsp_install_rust: true        # rust-analyzer, with cargo, rustc and the std source it needs
```

They are installed system-wide and linked into `/usr/local/bin`, so one install serves Neovim,
Emacs and the agents alike — every one of them starts a server by command name from `PATH`. After
installing, the role completes a real LSP handshake with each server as an unprivileged user, and
`test-lsp-verify.yml` asks them for actual diagnostics. Distros that lack a package (Debian 12 and
Ubuntu have no usable `rust-analyzer`; Debian and Ubuntu have no `ruff`) get the official builds
instead: `lsp_rust_toolchain: "1.100.0"`, `lsp_ruff_version: "0.17.6"`. The role README has the
lines each editor and agent needs to start using them.

## Secrets & Vault

The split is deliberate: **structure is committed, values never are.**

- **Where the vault lives:** `group_vars/paseo_fleet/vault.yml` on the **controller**, encrypted with
  `ansible-vault` (AES-256). It is **gitignored** and **never copied to targets**.
- **At run time:** Ansible decrypts it in memory; the values are written to each target at
  `/etc/paseo/paseo.env` (`0640 root:paseo`), loaded by the service.
- **The vault password** is resolved by [`vault-pass.sh`](vault-pass.sh) (wired via `ansible.cfg`), in
  order: `$ANSIBLE_VAULT_PASSWORD` → `./.vault_pass` → system keyring. Set up **one** source once and
  you never pass `--ask-vault-pass`; the password itself is never stored in the repo. Ansible reads it
  for every command, so set a source before running — `vault-pass.sh` prints how if you haven't.

To rotate or add keys: edit `~/.config/opencode/.env`, re-run `./make-vault.sh`, re-run the play.

## Helper scripts

| Script | What it does |
|---|---|
| [`make-vault.sh`](make-vault.sh) | One shot: `.env` → encrypted `group_vars/paseo_fleet/vault.yml` (derives the daemon password from `PASEO_TOKEN`). Secret values never print. |
| [`opencode-secrets-from-env.sh`](opencode-secrets-from-env.sh) | `.env` → a `paseo_opencode_secrets:` YAML block (the piece `make-vault.sh` uses). |
| [`vault-pass.sh`](vault-pass.sh) | Resolves the Ansible Vault password (env / file / keyring / prompt). No secret in the script. |
| [`redact-json.py`](redact-json.py) | Strip bcrypt hashes / credentials from a JSON config for safe export. |
| [`opencode-effective-config.py`](opencode-effective-config.py) | Reproduce OpenCode's merged effective config (jsonc-aware), redacted by default. |

## Supported platforms

| Init system | Distros |
|---|---|
| systemd **or** OpenRC (auto-detected) | Gentoo · Debian/Ubuntu · Fedora/RHEL · Arch |

Node ≥ 22 is required (the Paseo CLI's prompts use `util.styleText`'s array form). Fedora and Arch
ship Node ≥22; on Debian/Ubuntu the distro Node is usually too old — install from NodeSource, or set
`paseo_manage_node: false` and provide your own.

## What's verified

Confirmed empirically against `@getpaseo/cli` 0.17.4 — by reading the published package, loading
the role's `config.json` in a live daemon, and running the role end to end on Fedora 44 + systemd:

- **Foreground mode is `paseo daemon run`** (from 0.9.0). `daemon start --foreground`, which this
  repo used before, now exits 1 with *"--foreground was removed"*. The role asks the installed CLI
  which form it has, so hosts on either side of 0.9.0 work.
- The password is bcrypt (cost 12) at `config.json` → `daemon.auth.password`; `set-password` is an
  un-pipeable TTY prompt, so the role hashes with Paseo's own `hashDaemonPassword()` on the target.
  A generated config + hash were loaded by the **live daemon**: `/api/health` returned `200`, the
  correct password authenticated, a wrong one was rejected.
- The config schema is **strict** — the role writes only the keys it validates against
  (`daemon.listen` / `daemon.hostnames` / `worktrees.root` / `daemon.auth.password` / `daemon.relay` /
  `agents.providers.<id>`).
- Agent providers: with Claude Code and Kimi Code enabled, the running daemon reports both as
  available, and a second play run changes nothing.
- **Note:** Paseo auto-injects its own MCP (with a capability token) into agents it launches, so a
  manual `mcp.paseo` block in `opencode.jsonc` is usually redundant. See `paseo/README.md`.

## Security

- Only **sanitized** config is committed: `opencode.jsonc` uses `{env:VAR}` placeholders (audited — no
  inline keys), and only `vault.yml.example` (empty placeholders) is tracked.
- [`.gitignore`](.gitignore) keeps vault files, `.vault_pass*`, `.env`, `auth.json`, `*.unvaulted.yml`,
  fetched host artifacts, and `*.bak` out of git. Every secret-touching task is `no_log`.
- The daemon `config.json` and the secrets env file are `0600`/`0640`, owned by the `paseo` user.

## Further reading

- [`paseo/README.md`](paseo/README.md) — the role reference: every variable, the OpenCode wiring
  modes (discover / fetch / print / MCP), Claude Code and Kimi Code, own-user mode, and the
  daemon-config export options.
- [`editors/README.md`](editors/README.md) — Neovim, Emacs and Doom Emacs: sources, pinning, and
  what was actually tested.
- [`lsp/README.md`](lsp/README.md) — language servers for Python and Rust: where each comes from,
  how they are verified, and how Neovim, Emacs, OpenCode and Claude Code pick them up.
- [`paseo/example/`](paseo/example/) — a library-style three-box example that drives the role directly.

## License

MIT (see `paseo/meta/main.yml`). Built from the `paseo-gentoo-netbird` runbook, with correctness
fixes folded into the units.
