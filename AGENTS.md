# AGENTS.md

Guidance for coding agents working in this repository.

## Project overview

This repository is an Ansible-based installer for a fleet of Paseo daemons that launch coding agents (OpenCode by default; Claude Code and Kimi Code opt-in) over a NetBird mesh, plus two opt-in roles for the same hosts: `editors` (Neovim, Emacs, Doom Emacs) and `lsp` (language servers for Python and Rust). It supports systemd and OpenRC targets across Gentoo, Debian/Ubuntu, Fedora/RHEL, and Arch.

The key invariant: the role templates configuration and service units, but not daemon state. Do not copy, template, or otherwise manage `daemon-keypair.json`; each host must generate its own daemon identity.

## Repository layout

- `site.yml` — main playbook; run from the repository root.
- `inventory.yml` — fleet inventory; one host per box with its own NetBird mesh IP.
- `ansible.cfg` — sets `roles_path = .`, `inventory = inventory.yml`, and `vault_password_file = ./vault-pass.sh`.
- `opencode.jsonc` — committed sanitized OpenCode config; secrets must remain `{env:VAR}` placeholders.
- `group_vars/paseo_fleet/vault.yml.example` — example vault structure. The real `vault.yml` is gitignored.
- `make-vault.sh` — builds encrypted `group_vars/paseo_fleet/vault.yml` from an OpenCode `.env`.
- `opencode-secrets-from-env.sh` — converts an OpenCode `.env` into a vault-ready `paseo_opencode_secrets:` YAML block. It prints secret values; treat output as sensitive.
- `vault-pass.sh` — resolves the Ansible Vault password from env, local file, keyring, or prompt.
- `redact-json.py` / `opencode-effective-config.py` — helper scripts for safe config inspection.
- `paseo/` — the Ansible role. Important subdirs:
  - `paseo/defaults/main.yml` — authoritative defaults and comments for role variables.
  - `paseo/tasks/` — install, password, config, service, OpenCode, Claude Code, Kimi Code, and fetch tasks.
  - `paseo/templates/` — systemd/OpenRC/env templates.
  - `paseo/files/` — standalone helper scripts deployed/used by the role.
  - `paseo/example/` — library-style example topology.
- `editors/` — a second, independent Ansible role (Neovim / Emacs / Doom Emacs). Same layout: `defaults/main.yml` is the commented list of tunables, `tasks/` holds the per-editor files, `README.md` records what was actually tested. Listed after `paseo` in `site.yml` because it defaults to that role's user.
- `lsp/` — a third, independent role: language servers installed system-wide (pyright + ruff for Python, rust-analyzer + toolchain for Rust). `lsp/files/lsp-probe.py` is the stdlib-only LSP client the role and `test-lsp-verify.yml` use to prove a server works. No per-user state and no dependency on the other roles.
- `test-*.yml` / `dev-*.yml` — single-host smoke-test playbooks; the `test-*-verify.yml` ones are read-only assertions against a provisioned host.
- `ansible-role-paseo.tar.gz` — packaged snapshot of the `paseo/` role only.

## Development commands

Run commands from the repository root unless noted.

- Install required Ansible collection:
  ```sh
  ansible-galaxy collection install -r paseo/requirements.yml
  ```
- Create/update encrypted vault from local OpenCode secrets:
  ```sh
  ./make-vault.sh
  ```
- Run the main playbook:
  ```sh
  ansible-playbook site.yml --ask-become-pass
  ```
- Manual vault workflow, if needed:
  ```sh
  ./opencode-secrets-from-env.sh ~/.config/opencode/.env >> group_vars/paseo_fleet/vault.yml
  ansible-vault encrypt group_vars/paseo_fleet/vault.yml
  ```
- Inspect an encrypted vault without exposing it in git:
  ```sh
  ansible-vault view group_vars/paseo_fleet/vault.yml
  ```
- Syntax-check the playbook when Ansible is available:
  ```sh
  ansible-playbook --syntax-check site.yml
  ```

There is no package manager, build step, unit-test suite, linter config, or formatter config in this repo. Prefer Ansible syntax checks and targeted script review over inventing unsupported commands.

## Secrets and safety rules

- Never commit real secrets. Keep only sanitized config in git.
- Do not read or print secret values unless explicitly necessary for the user’s request. Prefer key/shape checks over value output.
- The following are intentionally gitignored and should stay that way: `.env`, `.env.*`, `.vault_pass*`, `group_vars/*/vault.yml`, `group_vars/*/vault.yaml`, `host_vars/*/vault.yml`, `host_vars/*/vault.yaml`, `auth.json`, `*.unvaulted.yml`, `*.unvaulted.yaml`, fetched host artifacts, and `*.bak`.
- `opencode.jsonc` must use `{env:VAR}` placeholders for provider secrets. If a new placeholder is added, update the vault generation/docs accordingly.
- `group_vars/paseo_fleet/vault.yml` lives on the controller, is encrypted with Ansible Vault, and is not copied to targets. At runtime, selected values are written to `/etc/paseo/paseo.env` on targets.
- Secret-touching Ansible tasks should use `no_log`.
- `opencode-secrets-from-env.sh` prints secret values; pipe directly to a vault file and encrypt, or avoid running it in contexts where output may be captured.

## Ansible and role conventions

- Keep YAML explicit and readable. Existing files use `---` document starts and descriptive comments for operational gotchas.
- Use role defaults in `paseo/defaults/main.yml` (or `editors/defaults/main.yml`, `lsp/defaults/main.yml`) for tunables; keep playbook/inventory focused on deployment-specific overrides.
- Put features that are not about the Paseo daemon or its agents (editors, language tooling) in their own role next to `paseo/`, opt-in by default, rather than growing `paseo/`.
- Preserve idempotency. Changes should be safe to re-run across a fleet.
- Preserve support for both systemd and OpenRC unless a change is explicitly scoped to one init system.
- Preserve cross-distro behavior for Gentoo, Debian/Ubuntu, Fedora/RHEL, and Arch.
- Prefer per-host overrides in `inventory.yml` for mesh IPs and hostnames.
- Keep `paseo_listen` per host on the NetBird mesh IP by default. Avoid switching to `0.0.0.0` without calling out the security implications.
- The role should manage `config.json` and service files, not daemon identity state.
- The daemon password is written as a bcrypt hash through Paseo’s own hashing code; do not replace this with `paseo daemon set-password` automation. That command is documented here as an interactive TTY prompt and not suitable for noninteractive Ansible.
- Do not hand-add unverified keys to daemon `config.json`; the Paseo schema is strict at the root and under `daemon` (unknown keys are rejected). Keys under `agents.providers.<id>` are stripped rather than rejected, but still keep additions minimal and verified.
- `claude` is an opt-in second agent (`paseo_install_claude`, default false). Its tasks must stay gated, and `claude.yml` must run after `config.yml` because it merges into the same `config.json`.
- `kimi` is an opt-in third agent (`paseo_install_kimicode`, default false), with the same gating and ordering rules. Unlike `claude` it is **not** a Paseo builtin: its `agents.providers.kimi` entry must carry `extends: "acp"`, `label`, and an absolute `command`, or Paseo silently ignores it.
- Every task that writes `config.json` must produce identical bytes for identical data, ending in exactly one newline. A mismatch makes the file flip on every play and restarts the daemon each time.
- When adding an agent, add its secrets dict to the union in `paseo/tasks/service.yml`, its bin dir to `paseo_agent_path_dirs`, and its bin dir to the SELinux relabel list — all three, or the agent half-works.
- No backslashes inside Jinja string literals (`regex_search('[.]')`, not `'\.'`; `'[{]'`, not `'\{'`). ansible-core 2.19+ reads an escape one way in a bare `{{ expression }}` and another once the template contains a `{% statement %}`, so a regex that works in one task silently matches nothing in the next. Character classes mean the same everywhere.
- Language servers belong in `lsp/` and are installed system-wide, with their commands linked into `lsp_bin_dir` (`/usr/local/bin`). Every client here — Neovim, Eglot, OpenCode, Claude Code's plugins — starts a server by command name from `PATH`, and that directory is on the daemon's `PATH` already. Do not make a server per-user or add a PATH knob to `paseo/` for one.
- A new server in `lsp/` is not done until it has a `verify.yml` handshake and a case in `test-lsp-verify.yml`. "On PATH" is not evidence: probe with `--version`, and run the handshake as `lsp_verify_user`, not root.
- In `lsp/`, anything root runs that creates files other users must read (npm, rustup) is prefixed with `umask 022`; and the official Rust toolchain is linked by its real binaries, never by rustup's shims.

## Paseo/OpenCode gotchas

- Node >= 22 is required for the Paseo CLI. The role defaults to `paseo_min_node_major: 22` and version-gates even when `paseo_manage_node: false`.
- The systemd unit needs the daemon in the foreground so restart behavior supervises a live process. The sub-command for that is version-dependent — `paseo daemon run` from `@getpaseo/cli` 0.9.0, `paseo daemon start --foreground` before it, and each fails on the other side — so `paseo/tasks/service.yml` detects it from the installed CLI. Do not hardcode either form.
- `paseo_cli_version: ""` upgrades the Paseo CLI on every play. After any Paseo upgrade, re-verify the things this role depends on (foreground command, `hashDaemonPassword()`, the config schema) against the new package before trusting a fleet run.
- Do not put the daemon's own setting names (`PASEO_PASSWORD`, `PASEO_LISTEN`, `PASEO_HOSTNAMES`, `PORT`, …) in any agent-secrets dict: the daemon acts on them (overriding `config.json` under systemd, stripping them under OpenRC) instead of passing them to the agent. Use a neutral name such as `PASEO_TOKEN`.
- Kimi Code does not read provider credentials from the environment. Only the `KIMI_MODEL_*` family (enabled by `KIMI_MODEL_NAME`), a `config.toml` provider with `api_key_env`, or an OAuth `kimi login` work. Its `config.toml` is rewritten by Kimi itself, so the role only seeds it.
- NetBird is not installed or managed by this role; services are only ordered after it.
- The service PATH intentionally includes each enabled agent's install dir and the Paseo npm prefix so Paseo can spawn `opencode`/`claude`/`kimi`. Build it from the `paseo_agent_path_dirs` list and keep the `reject('eq','')` filter: a disabled agent must not leave an empty `::` element, because an empty PATH element means the current directory.
- Agent-provider availability is decided by `which -a <cli>` run inside the daemon process, so only the daemon's own PATH matters. Setting a PATH in `agents.providers.<id>.env` does not affect discovery. Availability also ignores auth, so "ready" does not mean the credential works.
- With `ProtectHome=true`, the default `paseo_home_dir` under `/var/lib/paseo` is intentional. If switching to a login user under `/home`, set `paseo_systemd_protect_home: false`.
- Paseo may auto-inject its own MCP into launched agents. A manual `mcp.paseo` block in `opencode.jsonc` is often redundant; check `README.md` and `paseo/README.md` before changing this behavior.
- If a manual MCP block points at `127.0.0.1:6767` while the daemon binds only a mesh IP, the launched OpenCode cannot reach it over loopback. Use a mesh URL per host or bind appropriately with password/firewall boundaries.
- For remote `paseo run` or `schedule create --host` automation, include `--cwd`; remote commands do not inherit a shell working directory.

## Language-server gotchas

- A rustup shim is on `PATH` whether or not the component exists; `rust-analyzer --version` then fails with "Unknown binary 'rust-analyzer' in official toolchain". This is why `lsp/tasks/rust_probe.yml` runs the binary instead of looking for it.
- rust-analyzer without `rust-src` starts, handshakes and answers nothing about `std`. The role asserts the source tree is in the sysroot; `test-lsp-verify.yml` asks for a definition inside it.
- npm's global module root for a prefix is `<prefix>/lib` on most distros and `<prefix>/lib64` on Gentoo. Ask `npm root --global --prefix …`; never build the path.
- `pyright` and `basedpyright` both install `pyright` and `pyright-langserver`, so they cannot share an npm prefix. Switching `lsp_pyright_package` removes the other and then clears the dead links.
- On Gentoo `rust-analyzer` and `rust-src` are USE flags of the Rust package, not packages. The role emerges with `changed_use`, which rebuilds only when the flags changed — a compile for a source-built `dev-lang/rust`.
- OpenCode 1.x runs language servers only with `"lsp": true` in its config, and OpenCode 2 not at all. Claude Code needs the `pyright-lsp` / `rust-analyzer-lsp` plugins, declared through `paseo_claude_settings_extra` together with `env.CLAUDE_CODE_SYNC_PLUGIN_INSTALL: "1"`. Never `claude plugin install` for the daemon user: it rewrites the role-owned `settings.json`, and the next play restarts the daemon to put it back.

## Validation checklist

Before handing off changes:

1. Re-read relevant defaults, tasks, templates, and docs touched by the change.
2. Run `ansible-playbook --syntax-check site.yml` when Ansible and required collections are available. A syntax check proves very little here: for anything that changes what lands on a host, run the relevant `test-*.yml` play against one host (or a container) **twice** — the second run must report `changed=0` — and then its `test-*-verify.yml`.
3. If editing shell helpers, run `sh -n <script>` on changed POSIX shell scripts.
4. If editing Python helpers (including `lsp/files/lsp-probe.py`), run `python3 -m py_compile <script>` on changed Python files.
5. Verify no secret files or generated/fetched artifacts were added to git.
6. Review `git diff` for accidental secret output, host-specific state, or daemon identity material.

If validation cannot be run because required tools or credentials are unavailable, state that clearly and explain what was inspected instead.
