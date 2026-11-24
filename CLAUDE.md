# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `AGENTS.md` too — its secrets/safety rules, Ansible conventions, and validation checklist apply to all work here. This file adds the commands and cross-file architecture it doesn't cover.

## What this repo is

Ansible automation that deploys a fleet of [Paseo](https://getpaseo.com) daemons plus the agent CLIs they launch — [OpenCode](https://opencode.ai) by default, Claude Code and Kimi Code opt-in — reachable over a NetBird mesh, on systemd **or** OpenRC (auto-detected per host) across Gentoo, Debian/Ubuntu, Fedora/RHEL, and Arch. A second role, `editors/`, adds Neovim / Emacs / Doom Emacs to the same hosts, and a third, `lsp/`, installs language servers for Python and Rust. There is no package manager, build step, unit-test suite, linter, or formatter — validation is Ansible syntax checks, shell/Python compile checks, and the smoke-test playbooks below.

## Claude → Codex orchestration (Pattern A)

This repo includes a portable Claude-orchestrates / Codex-implements kit:

- Short pointer: [`.claude/README-codex-orchestration.md`](.claude/README-codex-orchestration.md)
- Full guide: [`docs/guides/CLAUDE_CODEX_ORCHESTRATION.md`](docs/guides/CLAUDE_CODEX_ORCHESTRATION.md)
- Skills: `/codex-impl` (one feature), `/features-parallel` (many)
- Scripts: `.claude/scripts/feature-worktree.sh`, `feature-status.sh`, `.claude/skills/codex-impl/scripts/run-codex.sh`

Main checkout stays put; features run in `.worktrees/<slug>` on `feat/<slug>`. Prefer `ansible-playbook --syntax-check` / `sh -n` / `py_compile` for verify — not heavy debug builds.

## Commands

All commands run from the repo root (`ansible.cfg` sets `roles_path = .`, `inventory = inventory.yml`, `vault_password_file = ./vault-pass.sh`).

**Vault password prerequisite:** Ansible executes `vault-pass.sh` eagerly for *every* command and requires a non-empty result. Before running anything, one source must exist: `$ANSIBLE_VAULT_PASSWORD`, a `./.vault_pass` file, or the keyring (`secret-tool store --label="paseo ansible vault" service ansible-vault key paseo`).

```sh
# One-time: install the required collection (community.general)
ansible-galaxy collection install -r paseo/requirements.yml

# Syntax-check (the closest thing to a fast "test")
ansible-playbook --syntax-check site.yml

# Full fleet deploy
ansible-playbook site.yml --ask-become-pass

# Single-host smoke tests — run the full role against ONE inventory host
# (real daemon + service; no OpenCode provider secrets needed, throwaway passwords inline)
ansible-playbook test-gentoo.yml --ask-become-pass   # hosts: gentoo
ansible-playbook test-arch.yml --ask-become-pass     # hosts: archtest
ansible-playbook dev-arch.yml --ask-become-pass      # hosts: arch
ansible-playbook dev-desk.yml --ask-become-pass      # hosts: fed4090

# Per-feature smoke tests (hosts: all — always pass --limit) and their read-only verify plays
ansible-playbook test-claude.yml   --limit <host>  &&  ansible-playbook test-claude-verify.yml   --limit <host>
ansible-playbook test-kimicode.yml --limit <host>  &&  ansible-playbook test-kimicode-verify.yml --limit <host>
ansible-playbook test-editors.yml  --limit <host>
ansible-playbook test-lsp.yml      --limit <host>  &&  ansible-playbook test-lsp-verify.yml      --limit <host>

# Vault management (see the make-vault.sh caveat under "Load-bearing design decisions")
./make-vault.sh                                      # ~/.config/opencode/.env → encrypted group_vars/paseo_fleet/vault.yml
ansible-vault view group_vars/paseo_fleet/vault.yml
ansible-vault edit group_vars/paseo_fleet/vault.yml

# When editing helper scripts
sh -n <script.sh>
python3 -m py_compile <script.py>
```

Debugging a failing secret-bearing task: add `-e paseo_no_log=false` (output is censored by default via `paseo_no_log`). Never leave it off, and never commit runs/logs made with it off.

## Architecture

Four layers:

- **Repo root — the deployment.** `site.yml` (the play: roles `paseo`, `editors`, `lsp`) + `inventory.yml` (the real fleet: per-host mesh hostname, `paseo_listen`, SSH creds from vault) + `ansible.cfg` + vault plumbing + `opencode.jsonc` (the committed, sanitized OpenCode config — secrets are `{env:VAR}` placeholders only).
- **`paseo/` — a self-contained role** (`homelab.paseo`), also usable library-style (see `paseo/example/`). `paseo/defaults/main.yml` is the authoritative, commented list of every tunable.
- **`editors/` — a second, independent role** (`homelab.editors`): Neovim, Emacs, Doom Emacs. Entirely opt-in (does nothing with defaults). It has no dependency on `paseo`, but its `editors_user`/`editors_home` default to `paseo_user`/`paseo_home_dir` when both roles are in one play — so it must be listed *after* `paseo`. New non-Paseo dev-environment features belong in a role of their own like this one, not in `paseo/`.
- **`lsp/` — a third, independent role** (`homelab.lsp`): language servers, system-wide. Python gets pyright (npm, in its own prefix) and ruff; Rust gets rust-analyzer *with* cargo, rustc and `rust-src`, from the distro or as a pinned official toolchain. Opt-in, no per-user state, no dependency on either other role — its order in the play does not matter. See "The `lsp` role" below.

### Role task flow (`paseo/tasks/main.yml`)

1. Assert inputs; compute `paseo_effective_hostnames` — the listen IP is auto-appended to the Host-header allowlist (avoids the "open port + valid password, still rejected" trap).
2. `install.yml` — system `paseo` user (home `/var/lib/paseo` so `ProtectHome=true` still works), Node (per-distro branches, version-gated ≥22 even when `paseo_manage_node: false`), Paseo CLI into a per-user npm prefix. `npm root -g` is queried at runtime because lib64 profiles (Gentoo) put global modules under `lib64/`, and `password.yml` must `chdir` into the CLI package dir.
3. `opencode_install.yml` (only when `paseo_install_opencode`) — official installer (`curl | bash` into `~/.opencode/bin`, honors `VERSION=`); the install task itself re-runs only when OpenCode is missing, a pinned version differs, or `paseo_opencode_upgrade` is set.
4. `claude_install.yml` (only when `paseo_install_claude`, default **false**) — Anthropic's native installer (`curl | bash -s <version>`); idempotency reads the launcher symlink with `readlink -e` so a no-op play never execs the ~260 MB binary. When a binary already exists it calls `claude install <version>` instead, because the bootstrap always downloads `latest` before the pin.
5. `kimicode_install.yml` (only when `paseo_install_kimicode`, default **false**) — Moonshot's official installer (`curl | bash`, honors `KIMI_VERSION`) into `~/.kimi-code/bin/kimi`. Always passes `KIMI_NO_MODIFY_PATH`: left alone the installer edits the user's shell rc. Idempotency execs `kimi --version` (no version-named symlink to read, unlike Claude).
6. `password.yml` (only when `paseo_set_password`, default true) — see below.
7. `config.yml` — deep-merges only owned keys into `config.json`: `daemon.listen`, `daemon.hostnames`, `daemon.relay.enabled`, `worktrees.root`. Everything else (the password hash, unknown keys) survives.
8. `opencode.yml` — deploy the sanitized config (+ optional `auth.json` from vault), and the optional discover/fetch/MCP-merge/print paths.
9. `claude.yml` (only when `paseo_install_claude`) — **must run after `config.yml`**, since it deep-merges `agents.providers.claude.command` into the same `config.json`. Also deploys the role-owned `~/.claude/settings.json` and merges the onboarding flag into `~/.claude.json`.
10. `kimicode.yml` (only when `paseo_install_kimicode`) — also **after `config.yml`**: deep-merges `agents.providers.kimi` (a custom ACP provider), optionally *seeds* `~/.kimi-code/config.toml`, and runs a non-fatal credential preflight.
11. `fetch_daemon_config.yml` (only when `paseo_fetch_daemon_config`, default false), `selinux.yml` (`paseo_manage_selinux` + RedHat + SELinux enabled: relabel each enabled agent's bin dir `bin_t`), then `service.yml` — env file (only when the **union** of `paseo_opencode_secrets`, `paseo_claude_secrets` and `paseo_kimicode_secrets` is non-empty; units tolerate its absence), a probe of the installed CLI for its foreground sub-command (systemd only), systemd unit or OpenRC init.d + conf.d, flush handlers, enable+start.

### Load-bearing design decisions

- **Config, never state.** The role manages `config.json` and service units but must never copy/template `daemon-keypair.json` — each host generates its own identity. The daemon's config schema is **strict**: don't add unverified keys or the daemon refuses to start.
- **The password is written, not prompted.** `paseo daemon set-password` is an interactive TTY prompt (no flags) — never automate it. `password.yml` runs Paseo's own `hashDaemonPassword()` (bcryptjs cost 12) via `node` from inside the installed CLI package on the target and merges the hash into `config.json` at `daemon.auth.password`. A sha256 marker file (`.ansible_pw_marker`) gates the work so the bcrypt salt only changes on real rotation (idempotent re-runs).
- **Secrets pipeline (structure is committed, values never are):** `~/.config/opencode/.env` → `make-vault.sh` → encrypted `group_vars/paseo_fleet/vault.yml` (controller-only, gitignored) → decrypted in memory at run time → written to `/etc/paseo/paseo.env` (0640 root:paseo) on each target → loaded by both init systems (systemd `EnvironmentFile=`, OpenRC sourcing) → Paseo launches OpenCode with that env, so `{env:VAR}` in `opencode.jsonc` resolves.
- **`make-vault.sh` does not produce the whole vault this fleet needs.** It writes `paseo_opencode_secrets` plus a single unsuffixed `vault_paseo_daemon_password` (derived from `PASEO_TOKEN` in the `.env`) — but that unsuffixed var is consumed only by `paseo/example/site.yml`. The real `inventory.yml` consumes per-host `vault_paseo_daemon_password_<host>` and `vault_ssh_password_<host>` vars, which the script never generates. It also **replaces** `vault.yml` wholesale (previous file → `.bak`, gitignored). After re-running it, re-add the per-host vars with `ansible-vault edit` or recover them from the `.bak`.
- **Daemon passwords are per host.** `inventory.yml` maps `paseo_daemon_password` to `vault_paseo_daemon_password_<host>` vars. `site.yml` deliberately sets no play-level password — play vars would override the per-host ones. Keep it that way.
- **Init-system duality.** `paseo_init_system` auto-detects from `ansible_service_mgr`; every service-level feature (env file, NetBird ordering, PATH) must work on both systemd and OpenRC. systemd runs the daemon in the **foreground** (`daemon start` self-detaches and exits 0; under `Type=simple` that SIGTERMs the real worker). Both units get `paseo_service_path` baked in (each enabled agent's bin dir + npm prefix bin first) so the spawned agent CLI is found. Units order after NetBird so the mesh IP exists before bind (`EADDRNOTAVAIL` otherwise).
- **The foreground sub-command is detected, never hardcoded.** `@getpaseo/cli` 0.9.0 replaced `daemon start --foreground` with `daemon run`; the old flag is now a tombstone that exits 1, and `daemon run` does not exist before 0.9.0 — the two are mutually exclusive. `service.yml` runs `paseo daemon run --help` and matches the sub-command's **own usage line**, not the exit code: on a CLI without `run`, commander prints the parent `daemon` help and still exits 0. `paseo_daemon_foreground_command` overrides the result. OpenRC uses plain `daemon start`, which means the same on every version.
- **`paseo_cli_version: ""` upgrades the CLI on every play** (`state: latest`). That is how the 0.9.0 change would have reached an unpinned fleet. Treat any Paseo upgrade as needing re-verification of what this role depends on: the foreground command, `hashDaemonPassword()` in `@getpaseo/server`, and `PersistedConfigSchema`.
- **Every writer of `config.json` must emit identical bytes for identical data.** `config.yml`, `password.yml`, `claude.yml` and `kimicode.yml` all merge into that one file. They must all end with one trailing newline (`content: "{{ … }}\n"`, or a `>` block — **not** `>-`): a mismatch makes each play flip the file twice, and every flip restarts the daemon. Appending `~ '\n'` inside a block scalar does *not* work — Ansible passes the backslash through literally and the result is invalid JSON.
- **`paseo_opencode_secrets` is replaced wholesale, not merged.** Ansible dicts don't merge across var files — if the vault defines it, a group-vars copy in `inventory.yml` is ignored. Keep all keys in one place.
- **One env file for all agents.** There is exactly one `EnvironmentFile=`/one sourced file, so `paseo_opencode_secrets`, `paseo_claude_secrets` and `paseo_kimicode_secrets` are unioned in `service.yml` (`_paseo_agent_secrets`) rather than each getting its own file. The gate tests the **union** — gating on the OpenCode dict alone would leave a Claude-only host with no env file, and `ANTHROPIC_API_KEY` would silently never reach the daemon. Key names must not collide across the dicts. **A new agent's secrets dict must be added to that union** or it is written nowhere (Kimi's was originally missed).
- **Daemon setting names are not agent secrets.** The daemon reads its own settings from the same environment (`PASEO_PASSWORD`, `PASEO_LISTEN`, `PASEO_HOSTNAMES`, `PORT`, …). In foreground mode (systemd) they override `config.json`; from 0.9.0 `daemon start` (OpenRC) strips them before launching anything. So such a key in a secrets dict does the wrong thing on both init systems. `service.yml` warns (list in `paseo/vars/main.yml`); use a neutral name like `PASEO_TOKEN`, which is why `paseo_opencode_mcp_token_env` defaults to it.
- **Kimi Code is a custom ACP provider, and each key of its entry is load-bearing.** `kimi` is not a builtin. `agents.providers.kimi` needs `extends: "acp"` (without it the entry validates and is then *silently skipped* — no provider, no error), `label` (required with `extends`, or the daemon rejects `config.json`), and `command` (absolute path + `acp`). The id must be exactly `kimi` — Paseo keys its Kimi ACP client on it. Verify provider changes against a **running daemon** (`paseo daemon status --json` lists providers), not just the schema.
- **Kimi Code does not read credentials from the environment.** A bare `KIMI_API_KEY`/`MOONSHOT_API_KEY` in the env file does nothing. Only the `KIMI_MODEL_*` family (switched on by `KIMI_MODEL_NAME`), a `config.toml` provider with `api_key_env`, or an OAuth `kimi login` work. And `~/.kimi-code/config.toml` is rewritten by Kimi itself (login, model refreshes), so the role only *seeds* it; `~/.kimi-code/credentials` is per-host state the role never touches.
- **PATH is the only agent-discovery lever.** Paseo decides a provider is "available" by running `which -a <cli>` **inside the daemon process**, so only the daemon's own PATH (`paseo_service_path`, baked into both units) counts. Setting a PATH in `agents.providers.<id>.env` does **not** work — that env reaches the spawned child, never the lookup. `paseo_service_path` is therefore built from the `paseo_agent_path_dirs` list and joined with `reject('eq','')`: a naive concatenation leaves an empty `::` element when an agent is disabled, and an empty PATH element means the **current directory** — a real privilege problem for a root-installed unit.
- **Auth is not part of availability.** A host with an agent CLI installed but no valid credential still reports *ready* and only fails at the first prompt. The availability probe also treats a non-zero exit *or* a 2-second hang as success, so *ready* means "a binary answered", not "a working install".
- **`hasCompletedOnboarding` is an unsupported bet.** Claude Code's first-run wizard blocks non-interactive use, and the flag that suppresses it (`~/.claude.json` → `hasCompletedOnboarding`) is undocumented internal state. The role read-modify-writes it (never templates that file — it also holds `machineID` and update bookkeeping) and this may break on a Claude Code upgrade. `paseo_seed_claude_onboarding: false` opts out.
- **Provider overrides are not strict, but the config root is.** Verified against the daemon's own `PersistedConfigSchema`: unknown keys at the root or under `daemon` are **rejected**, while unknown keys under `agents.providers.<id>` are silently stripped. `command` must be a non-empty array of strings — a bare string is rejected. The role sets only `command`, so there is less to break on a daemon upgrade.

### The `lsp` role

- **One install, every consumer.** Neovim, Eglot/Doom, OpenCode and Claude Code's LSP plugins all start a server by **command name from `PATH`** (`pyright-langserver`, `rust-analyzer`). So the role installs system-wide and links commands into `lsp_bin_dir` (`/usr/local/bin`) — a directory `paseo_service_path` already ends with. That is why there is no per-user install and no PATH coupling with `paseo/`; keep it that way.
- **"Works", not "exists".** A rustup shim is on `PATH` and exits 1 (*"Unknown binary 'rust-analyzer' in official toolchain"*) when the component was never installed — this workstation's own `~/.cargo/bin/rust-analyzer` does exactly that. `rust_probe.yml` therefore runs `rust-analyzer --version`, and `lsp_skip_if_present` skips only a *complete* working setup (server + cargo/rustc + `rust-src`).
- **Verification is a real LSP exchange, as an unprivileged user.** `verify.yml` runs `files/lsp-probe.py` (stdlib-only; the task is root, the *server* runs as `lsp_verify_user`, default `nobody`) and completes `initialize`. Running as `nobody` is what catches a tree only root can read. `test-lsp-verify.yml` goes further: a type error from each type checker, and go-to-definition landing in typeshed and in the std source. A new server needs both.
- **The official Rust toolchain is linked by its real binaries, not rustup's shims.** rustup is only the downloader, private under `lsp_rust_root`; `rustup which` locates the toolchain's `bin/` and each file there is linked into `lsp_bin_dir`. A real `rustc` finds its sysroot from its own location, so it works for every user and the daemon with no `RUSTUP_HOME`. The price is deliberate: nothing honours `rust-toolchain.toml` — the same contract as a distro package.
- **`umask 022` on everything root creates for others.** npm and rustup inherit root's umask; under a hardened `077` they produce trees nobody else can read. Both are run through `umask 022 && …`. Verified on a host whose every process has umask 077.
- **Ask npm where its modules are.** `<prefix>/lib/node_modules` on most distros, `<prefix>/lib64/node_modules` on Gentoo — the same lesson as `paseo_cli_pkg_dir`.
- **Gentoo has no rust-analyzer package.** It is a USE flag (with `rust-src`) on `dev-lang/rust` / `dev-lang/rust-bin`. The role writes `/etc/portage/package.use/coral-lsp-rust` and emerges with `changed_use: true`, which rebuilds only when the flags changed. For a source-built `dev-lang/rust` that rebuild is a full compile — not run here; the `rust-bin` path was.
- **Agents need opting in separately.** OpenCode 1.x only runs language servers with `"lsp": true` in `opencode.jsonc` (and OpenCode 2 never does — the role's installer stays on 1.x). Claude Code needs its `pyright-lsp` / `rust-analyzer-lsp` plugins, declared in `paseo_claude_settings_extra.enabledPlugins` **with** `env.CLAUDE_CODE_SYNC_PLUGIN_INSTALL: "1"`; without that flag a headless session installed nothing. `claude plugin install` must not be used for the daemon user — it rewrites the role-owned `settings.json`, and the next play restarts the daemon to restore it.

### Jinja escapes are not portable — use character classes

On ansible-core 2.19+ a backslash in a Jinja string literal means different things depending on the template around it: in a bare `{{ expression }}` `'\\{'` is passed through literally (and matches nothing), while the same literal inside a template that contains a `{% statement %}` is unescaped the classic way. A double-quoted YAML scalar adds a third layer. Write `[.]`, `[{]`, `[0-9]` instead of `\.`, `\{`, `\d`, and use `split | join(' ')` rather than a `\s+` replace. (The earlier "`~ '\n'` in a block scalar" bug was the same thing.)

### Duplicated files — keep in sync

`paseo-role-README.md`, `redact-json.py`, and `opencode-effective-config.py` at the repo root are byte-identical copies of `paseo/README.md`, `paseo/files/redact-json.py`, and `paseo/files/opencode-effective-config.py`. If you edit one, mirror the change. `ansible-role-paseo.tar.gz` is a packaged snapshot of `paseo/`.

## Secrets rules (critical)

- Only sanitized config is committed: `opencode.jsonc` must keep every secret as an `{env:VAR}` placeholder; only `vault.yml.example` is tracked. If you add a new `{env:NEW_VAR}` placeholder, the value goes in the `.env` → re-run `./make-vault.sh`.
- Never print secret values; prefer key/shape checks. `opencode-secrets-from-env.sh` prints values — pipe straight to a vault file.
- Secret-touching tasks use `no_log: "{{ paseo_no_log }}"` (or bare `no_log: true`) — preserve this on any new task that handles passwords/keys.
- Gitignored and must stay so: `.env` / `.env.*`, `.vault_pass` / `.vault_pass.*`, `group_vars/*/vault.yml`, `auth.json`, `*.unvaulted.yml`, `/fetched-opencode/`, `/fetched-daemon-config/`, `*.bak`.
