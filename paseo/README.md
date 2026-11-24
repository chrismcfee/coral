# ansible-role: paseo

Deploys a headless [Paseo](https://getpaseo.com) daemon as one-per-host over a NetBird mesh,
with optional OpenCode wiring. Built from the `paseo-gentoo-netbird` runbook (§8 outline), with
three correctness fixes folded into the units (see [Fixes baked in](#fixes-baked-in)).

The role templates **config, never state**: it manages `config.json` and the service unit, but
never touches `daemon-keypair.json`. Each box generates its own identity on first start, so you
can run the same play across the whole fleet without two daemons ever sharing an identity.

**Platforms:** systemd **or** OpenRC (auto-detected), on Gentoo, Debian/Ubuntu, Fedora/RHEL, and Arch.

## What it does

1. Creates a system `paseo` user (home under `/var/lib/paseo` so a hardened unit can write there).
2. Installs Node (≥22, version-gated) and the Paseo CLI into a per-user npm prefix.
3. **Downloads + installs the OpenCode CLI** (official installer → `~/.opencode/bin`, version-pinnable)
   for that user, and **deploys your sanitized `opencode.json[c]`** into its config dir.
4. *(opt-in)* **Installs Claude Code** (Anthropic's native installer, version-pinnable) for that user
   and registers it as a Paseo agent provider. See "Claude Code as a second agent" below.
5. *(opt-in)* **Installs Kimi Code** (Moonshot's official installer, version-pinnable) and registers
   it as a custom ACP provider. See "Kimi Code as a third agent" below.
6. **Injects your agent provider secrets** (OpenCode's `{env:VAR}` values, `ANTHROPIC_API_KEY`, the
   `KIMI_MODEL_*` family) into the daemon's environment — one file loaded by **both** systemd and
   OpenRC — so they resolve when Paseo launches the agent CLI.
7. Seeds + deep-merges `config.json` (listen on the mesh IP, Host-header allowlist, worktrees root),
   preserving the password hash and any keys it doesn't manage.
8. Sets the daemon password from Vault — idempotently, with rotation support.
9. Drops a systemd unit **or** OpenRC script (auto-detected) and enables/starts it.

## Quickstart

From the repo root — a ready-to-run `site.yml` + `inventory.yml` + `ansible.cfg` live there:

```bash
ansible-galaxy collection install -r paseo/requirements.yml

# 1. Your sanitized OpenCode config is at the repo root as opencode.jsonc (secrets already
#    replaced by {env:VAR}). Put real mesh IPs in inventory.yml.

# 2. Set a vault password source once (keyring/env/file — see vault-pass.sh), then build the encrypted
#    Vault from your OpenCode .env (derives the daemon password from PASEO_TOKEN). Values never print:
./make-vault.sh                       # writes group_vars/paseo_fleet/vault.yml (encrypted)
#    (or do it by hand: ./opencode-secrets-from-env.sh >> vault.yml ; ansible-vault encrypt vault.yml)

# 3. Go (systemd or OpenRC, auto-detected per host). The vault password auto-resolves via
#    vault-pass.sh (env var / .vault_pass / keyring) — no --ask-vault-pass needed.
ansible-playbook site.yml --ask-become-pass
```

`example/` keeps the original library-style three-box (`cargogen2` / `ryzen-4090` / `pi-livingroom`)
topology if you'd rather drive the role that way.

## Fixes baked in

These diverge from the runbook's literal unit files on purpose:

- **systemd runs Paseo in the foreground.** `paseo daemon start` self-detaches and exits 0. Under
  the runbook's `Type=simple` + bare `daemon start`, systemd reaps the launcher's exit and — with
  the default `KillMode=control-group` — SIGTERMs the real worker (or, if it escapes the cgroup,
  orphans it unsupervised). That's the same double-detach failure the runbook carefully avoids for
  OpenRC. The unit here runs the daemon in the foreground instead, so systemd supervises a process
  that stays alive. *How* to do that depends on the CLI — `daemon run` from `@getpaseo/cli` 0.9.0,
  `daemon start --foreground` before it, and the two are mutually exclusive — so the role asks the
  installed CLI and templates the matching form (see below).
- **Ordered after NetBird on both init systems.** The runbook's systemd unit only waits on
  `network-online.target`, which doesn't guarantee the WireGuard interface/IP is up — so a bind to
  `100.x` can still `EADDRNOTAVAIL`. This adds `After=`/`Wants=netbird.service` to match the OpenRC
  `after netbird`.
- **PATH baked into both units.** Neither runbook unit puts the npm prefix bin on PATH for *spawned*
  processes, so a per-user `opencode` isn't found when Paseo launches it. Both templates export
  `paseo_service_path` (npm prefix + `/usr/local/bin` first).

## Verified against @getpaseo/cli (0.17.4)

These were confirmed against the real CLI and daemon, not guessed — by reading the published
package, and by running the role end to end (Fedora 44, systemd) and loading its `config.json` in a
live daemon:

- **Foreground mode changed in 0.9.0, and the role detects which form the host has.**
  `paseo daemon start --foreground` is the flag up to 0.8.x. From 0.9.0 it is a tombstone that exits
  1 with *"--foreground was removed. Use paseo daemon run"* — a unit still using it crash-loops —
  and `paseo daemon run` is the foreground mode. Because an unpinned `paseo_cli_version` upgrades the
  CLI on every play, `tasks/service.yml` asks the installed binary and templates the matching
  `ExecStart`. The probe matches `daemon run`'s own usage line rather than the exit code: on a CLI
  without `run`, `daemon run --help` prints the parent help and still exits 0. Override with
  `paseo_daemon_foreground_command` if you ever need to. Both sides were run for real: a host
  provisioned with 0.1.101 got `daemon start --foreground`; re-running the play unpinned upgraded
  it to 0.17.4, rewrote the unit to `daemon run`, and the daemon came back healthy with the same
  password.
- **The password is written, not prompted.** `paseo daemon set-password` is still an interactive
  TTY prompt (it now says so itself: *"needs a terminal to read the password"*) — so the role never
  calls it. It hashes the password with paseo's own `hashDaemonPassword()` (bcryptjs cost 12) on the
  target and writes it to `config.json` → `daemon.auth.password` (the exact key the daemon's strict
  schema validates). A live 0.17.4 daemon accepted the right password and rejected a wrong one.
  Rotation-safe via a sha256 marker; nothing is logged.
- **The daemon's environment is not just a pass-through.** The daemon reads its own settings from
  the environment (`PASEO_PASSWORD`, `PASEO_LISTEN`, `PASEO_HOSTNAMES`, `PORT`, …). In foreground
  mode — systemd — they **override `config.json`**; from 0.9.0 `daemon start` — OpenRC — **strips**
  them before launching anything. So never use those names for agent secrets: `PASEO_PASSWORD` in
  the env file silently replaces the password this role manages on one init system and vanishes on
  the other. Use a neutral name such as `PASEO_TOKEN`. The role warns if it sees one.
- **Custom providers need `extends`.** An `agents.providers.<id>` entry for a non-builtin id is only
  a provider when it has `extends`; with just `command` it validates and is then silently skipped.
  `extends` in turn requires `label`, and `extends: "acp"` requires `command`. See the Kimi section.
- **Node ≥ 22 is required** (`paseo_min_node_major: 22`). The CLI's prompts use `util.styleText`'s
  array form, which only exists on Node 22+. Fedora and Arch ship Node ≥22 (distro packages are fine);
  on Debian/Ubuntu the distro Node is too old — install from NodeSource (or set
  `paseo_manage_node: false` and provide your own ≥22).
- **Config keys are confirmed:** `daemon.listen` (string), `daemon.hostnames` (array or `true`),
  `daemon.relay.enabled`, `worktrees.root`, `daemon.auth.password`, `agents.providers.<id>`. The
  schema is **strict** at the root and under `daemon` — the role writes only known keys, so don't
  hand-add others to `config.json` or the daemon will refuse to start.
- **Every writer of `config.json` emits the same bytes.** Four task files merge into that file. If
  two of them disagree on so much as a trailing newline, each play flips the file twice and every
  flip restarts the daemon — which is what used to happen on any host with Claude Code enabled.
- **`PASEO_HOME` is `0700`.** The daemon re-tightens it to owner-only at startup; a looser mode in
  the role would be undone and reported as a change on every play.

## Out of scope (manage separately)

- **NetBird itself** — must be installed and up; the role only *orders after* it.
- **OpenCode is now installed + configured by the role** — see
  [OpenCode: install + deploy](#opencode-install--deploy). Still yours to provide: the *contents* of
  your sanitized `opencode.json[c]` and the secret *values* (via Vault). If a provider authenticates
  through `opencode auth login` rather than an `{env:VAR}` apiKey, hand the role its `auth.json` via
  `paseo_opencode_auth` (Vault); otherwise that one piece stays out of scope.
- **`daemon-keypair.json`** — deliberately never templated or copied.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `paseo_listen` | `127.0.0.1:6767` | **Override per host** to the mesh IP. The IP is auto-added to the allowlist. |
| `paseo_hostnames` | `[localhost, 127.0.0.1]` | Extra Host-header names; the listen IP is appended for you. |
| `paseo_relay_enabled` | `false` | Paseo's hosted relay (app.paseo.sh). Off for mesh-only fleets. |
| `paseo_min_node_major` | `22` | Confirmed required — the CLI's prompts need Node ≥22. |
| `paseo_set_password` | `true` | `false` = rely on the mesh as the sole boundary (no password). |
| `paseo_daemon_password` | `""` | Required when above is true — pull from Vault. |
| `paseo_daemon_foreground_command` | `""` | Empty = ask the installed CLI (`daemon run` on ≥ 0.9.0, else `daemon start --foreground`). Set to force one. |
| `paseo_foreground_flag` | `--foreground` | Legacy only: appended to `daemon start` on a CLI older than 0.9.0. |
| `paseo_init_system` | auto | `systemd` or `openrc` from `ansible_service_mgr`. |
| `paseo_after_netbird` / `paseo_netbird_unit` | `true` / `netbird` | Ordering dependency. |
| `paseo_manage_node` | `true` | `false` if you provide Node yourself (version still gated). |
| `paseo_cli_version` | `""` | Empty tracks latest — the CLI is upgraded on **every play**. Pin (e.g. `0.17.4`) for a reproducible fleet. |
| `paseo_opencode_bin` | `""` | If set, symlinks a pre-existing opencode binary into `/usr/local/bin`. |
| `paseo_install_opencode` | `true` | Download+install OpenCode (official installer) for `paseo_user`. |
| `paseo_opencode_version` | `""` | Pin OpenCode (e.g. `1.17.9`); empty = latest at first install. |
| `paseo_deploy_opencode_config` | `true` | Deploy your sanitized config (needs `paseo_opencode_config_src`). |
| `paseo_opencode_config_src` | `opencode.json` | Sanitized config on the controller; the repo `site.yml` points it at `{{ playbook_dir }}/opencode.jsonc`. |
| `paseo_opencode_secrets` | `{}` | `{env:VAR}` → value map (Vault); injected into the daemon environment. |
| `paseo_opencode_auth` | `{}` | Optional `auth.json` dict (Vault) for providers using `opencode auth login`. |
| `paseo_install_claude` | `false` | Install Claude Code (native installer) for `paseo_user` and register it with Paseo. |
| `paseo_claude_version` | `stable` | `stable` / `latest` / an exact semver. **Pin an exact version for a fleet.** |
| `paseo_claude_secrets` | `{}` | `ANTHROPIC_API_KEY` etc. (Vault); merged into the same daemon env file. |
| `paseo_verify_claude_credential` | `true` | Preflight the key and *report* the result. Never fails the play. |
| `paseo_claude_settings` | `{env: {DISABLE_AUTOUPDATER: "1"}}` | Role-owned `~/.claude/settings.json`. Extend via `paseo_claude_settings_extra`. |
| `paseo_install_kimicode` | `false` | Install Kimi Code (official installer) for `paseo_user` and register it with Paseo. |
| `paseo_kimicode_version` | `""` | Pin (e.g. `2.7.3`); empty = latest at first install. **Pin for a fleet.** |
| `paseo_kimicode_secrets` | `{}` | The `KIMI_MODEL_*` family, or the env var a `config.toml` provider names (Vault). Same env file. |
| `paseo_kimicode_config_src` | `""` | Optional sanitized `config.toml` to seed. Empty = Kimi manages its own. |
| `paseo_verify_kimicode_credential` | `true` | Preflight the `KIMI_MODEL_*` key and *report*. Never fails the play. |
| `paseo_systemd_protect_home` | `true` | See own-user mode below. |
| `paseo_home_dir` / `paseo_state_dir` | `/var/lib/paseo` / `…/.paseo` | Distinct `state_dir` per daemon for multiple-on-one-host. |

Full list with comments in `defaults/main.yml`.

## Own-user mode (Direction A without duplication)

If you'd rather the daemon inherit your existing OpenCode env (runbook §6's "your tools, your
configs" path), point the role at your login user:

```yaml
paseo_user: eris
paseo_group: eris
paseo_home_dir: /home/eris
paseo_systemd_protect_home: false   # else opencode can't read ~/.config & ~/.local/share
```

`ProtectHome=true` hides `/home`, so it must be off when home lives there; `ReadWritePaths` follows
`paseo_home_dir` automatically. This trades isolation for zero env duplication.

## OpenCode: install + deploy

The role downloads OpenCode with the **official installer** (prebuilt binary → `~/.opencode/bin`,
honoring `VERSION=`; needs `curl` + `tar` and outbound access to github.com), then deploys *your*
config. Because `opencode.json` carries secrets, the split is:

- **the config file** (providers, models, plugins, MCP servers) is sanitized — every secret becomes
  an OpenCode `{env:VAR}` placeholder — and lives at the repo root as `opencode.jsonc`;
- **the secret values** live only in Ansible Vault and are injected into the **daemon's environment**
  (`/etc/paseo/paseo.env`, `0640 root:paseo`, loaded by both init systems), so `{env:VAR}` resolves
  when Paseo launches OpenCode.

Nothing secret is ever templated into the config or committed.

### Secrets → Vault

Keep the real values in `~/.config/opencode/.env` (`export NAME="value"`, sourced from your shell rc
for interactive use), then turn that into the vault block — the keys must match the `{env:VAR}` names
in `opencode.jsonc`:

```sh
./opencode-secrets-from-env.sh ~/.config/opencode/.env >> group_vars/paseo_fleet/vault.yml
ansible-vault encrypt group_vars/paseo_fleet/vault.yml
```

These land in `paseo_opencode_secrets` and are written to the daemon env file (never logged).

### Provider credentials (auth.json)

Some providers don't take an apiKey in the config — they store creds in
`~/.local/share/opencode/auth.json` (what `opencode auth login` writes), which the dedicated `paseo`
user won't have. If your primary model needs it, paste that JSON as a dict into `paseo_opencode_auth`
(Vault) and the role writes it `0600`. Leave it empty if an `{env:VAR}` apiKey in the config suffices.

### Verify on your topology

- **Your manual `mcp.paseo` block is probably redundant.** Paseo auto-injects its own MCP into the
  agents it launches: the `/mcp/agents` endpoint is exempt from the daemon password and is
  authenticated by a per-daemon-run *capability token* Paseo injects itself — not your `PASEO_TOKEN`.
  So you can likely delete the `mcp.paseo` block (and `PASEO_TOKEN`) from `opencode.jsonc` and let
  Paseo wire the loopback with the correct local URL + token. Keep `daemon.mcp.injectIntoAgents` at
  its default (on) for that. This is also why hand-adding that one auth header never quite worked.
- **Loopback MCP vs mesh bind (only if you keep the manual block).** If your config points `mcp.paseo`
  at `127.0.0.1:6767` but a host binds only its mesh IP (`paseo_listen: 100.x:6767`), the OpenCode
  Paseo launches there can't reach Paseo on loopback. Fix per host by using the mesh IP in that URL,
  or `paseo_listen: "0.0.0.0:6767"` (password + firewall as the boundary).
- **`PASEO_TOKEN` == daemon password.** If OpenCode calls Paseo back with `Bearer {env:PASEO_TOKEN}`,
  set `PASEO_TOKEN` (in `paseo_opencode_secrets`) to the same value as `paseo_daemon_password`.
- **Own-user mode skips the transport.** Running the daemon as your login user (below) lets it reuse
  the `~/.config/opencode` + creds already on the box: set `paseo_deploy_opencode_config: false` and
  `paseo_install_opencode: false` if OpenCode's already there.

## OpenCode config

OpenCode's config location isn't fixed, so the role resolves it the way OpenCode does, via
`files/resolve-opencode-config.sh` (POSIX sh, standalone-usable):

1. `$OPENCODE_CONFIG` if set and present,
2. a project-local `opencode.json[c]` walking up from `paseo_opencode_config_cwd` (if given),
3. `$XDG_CONFIG_HOME/opencode/opencode.json[c]` (default `~/.config/opencode/`),
4. else the default would-be path (so a fresh file can be created there).

It resolves for **the user the daemon runs as** (`paseo_user`), with that user's HOME. Three things
you can do, independently or together:

- **Discover** (default on): leave `paseo_opencode_config_path` empty and the role fills it in. Set
  it explicitly to skip discovery and pin a file.
- **Fetch it back**: `paseo_fetch_opencode_config: true` pulls the resolved config to the controller
  at `<paseo_opencode_config_fetch_dir>/<host>/<remote/path>` — per host, so a fleet run doesn't
  clobber. (Creds under `~/.local/share/opencode/` are deliberately **not** fetched; use the script's
  `--data` flag by hand if you need that dir.)
- **Wire Direction B**: `paseo_manage_opencode_mcp: true` deep-merges only the `mcp.paseo` block
  (`oauth:false` + a `Bearer {env:PASEO_TOKEN}` header) into the resolved file, creating it if absent
  and leaving everything else untouched. The env var is `paseo_opencode_mcp_token_env`; it used to be
  hardcoded to `PASEO_PASSWORD`, which the daemon claims for itself (see "Verified" above).
- **Print the effective config**: `paseo_print_opencode_config: true` reproduces opencode's
  global + project + `$OPENCODE_CONFIG` merge from the files (jsonc-aware) and prints the resolved
  *values* to the play output. Secrets are redacted unless `paseo_print_opencode_config_raw: true`.
  This is a file-level approximation of opencode's resolution — verify against `opencode mcp list`
  if exact semantics matter.

Standalone, on any box:

```sh
# where does opencode read its config?
./resolve-opencode-config.sh --explain
# where are its creds?
./resolve-opencode-config.sh --data
# include a project-local config in the search
./resolve-opencode-config.sh --cwd /path/to/project

# print the effective (merged) config values, redacted
./opencode-effective-config.py --explain
./opencode-effective-config.py --cwd /path/to/project --raw   # include secrets
```

## Claude Code as a second agent

`claude` is a **first-class builtin provider** in Paseo, alongside `codex`/`copilot`/`opencode`. You
don't have to teach Paseo about it — but the daemon only reports it *available* when it can actually
find the binary, so the role installs it, puts it on the daemon's PATH, and pins its absolute path.

Off by default (it pulls a ~260 MB binary per host). Turn it on with:

```yaml
paseo_install_claude: true
paseo_claude_version: "2.1.325"        # pin exactly; `stable`/`latest` drift between hosts
paseo_claude_secrets:                   # from Vault
  ANTHROPIC_API_KEY: "{{ vault_anthropic_api_key }}"
```

Install is Anthropic's **native installer** (`claude.ai/install.sh`): a self-contained binary at
`~/.local/share/claude/versions/<v>` with `~/.local/bin/claude` symlinked to it. No Node at runtime,
`ripgrep` embedded. Needs `bash`, `curl`, `sha256sum`, ~512 MB free RAM, and outbound HTTPS.
Anthropic also ships signed apt/dnf/apk repos, but **not** for Gentoo or Arch — the native installer
is the only uniform path across all four families this role supports.

Idempotency reads the launcher **symlink** (`readlink -e`), so a no-op play never pays the binary's
cold start. On a host that already has `claude`, the role calls `claude install <version>` rather
than re-running the bootstrap — the bootstrap always downloads `latest` first *and then* the pin, so
it costs two full downloads.

**Moving a fleet to a newer Claude Code.** A channel (`stable` / `latest`) is only resolved at
*first* install: an installed version number can't be compared with the word "stable", so a host
that already has Claude Code is left alone, and `DISABLE_AUTOUPDATER` stops it updating itself.
Either bump an exact pin — the role then re-pins every host that differs — or run once with
`-e paseo_claude_upgrade=true`.

### How Paseo finds it

Availability is decided by `which -a claude` run **inside the daemon process**, so the only thing
that matters is the daemon's own PATH — `paseo_service_path`, baked into the unit. Setting a PATH in
`agents.providers.claude.env` will *not* work: that env reaches the spawned child, never the lookup.
The role covers this three ways: the bin dir goes on `paseo_service_path`, the binary is symlinked
into `/usr/local/bin`, and `agents.providers.claude.command` pins the absolute path (verified
against the daemon's schema — a `command` array skips PATH resolution entirely).

Two behaviours worth expecting:

- **Auth is not part of availability.** A host with `claude` installed but no valid key still shows
  as *ready*, and only fails on the first prompt. Check it with `sudo -u paseo claude auth status`.
- The availability probe treats a non-zero exit *or* a 2-second hang as success, so *available* means
  "a binary answered", not "a working install".

### First-run gate

A fresh dedicated-user home shows an onboarding wizard that blocks non-interactive use. The role
merges `hasCompletedOnboarding: true` into `~/.claude.json` (read-modify-write — that file also
holds `machineID` and update state, so it is never templated). **That key is undocumented internal
state**, not a supported interface: it's a compatibility bet that may break on a Claude Code
upgrade. Set `paseo_seed_claude_onboarding: false` to run `claude` by hand once per host instead.

### Credentials

`paseo_claude_secrets` is merged with `paseo_opencode_secrets` into the **same** `/etc/paseo/paseo.env`
— there is one `EnvironmentFile=`/one sourced file, and Ansible dicts don't merge across var files,
so the union happens in `tasks/service.yml`. A Claude-only host still gets the env file.

For a gateway instead of a direct key, set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` in the same
dict. Note Claude Code speaks the **Anthropic Messages API** (`POST /v1/messages`), so an
OpenAI-compatible endpoint needs a translating proxy in front of it.

### A bad credential never fails the play

An invalid key can't break provisioning: it is only ever *written* to `/etc/paseo/paseo.env`, and no
task authenticates with it. The risk is the opposite one — a dead key is invisible, because Paseo
reports the provider available on the strength of finding the binary.

So `paseo_verify_claude_credential` (default on) sends one `max_tokens: 1` request from the target
and **reports** what came back, without ever failing:

| Result | Meaning |
|---|---|
| `200` | Credential works. |
| `401` | Loud warning: key rejected. Install is fine, prompts will fail. **Play continues.** |
| `000` | Couldn't reach the endpoint at all. Says nothing about the key. |
| other | `403` = authenticated but not permitted; `404` = retired probe model; `400` = probe body rejected. Not evidence of a bad key. |

Only `401` is treated as a real verdict, so retiring `paseo_claude_preflight_model` upstream produces
a `404` "could not verify" rather than a false alarm. Set `paseo_verify_claude_credential: false` to
skip the request entirely (air-gapped fleets, or to avoid the round trip).

The probe uses `curl --config -` rather than Ansible's `uri` module, for two reasons found by
testing: `uri` aborts before sending anything if the target has a too-permissive `~/.netrc` (which
would silently downgrade every check to "could not verify"), and reading the header from stdin keeps
the credential out of `argv`, so it never appears in the process table on a shared host.

## Kimi Code as a third agent

[Kimi Code](https://moonshotai.github.io/kimi-code/) is **not** a Paseo builtin. Paseo drives it over
ACP (`kimi acp`, JSON-RPC on stdio), and only once `config.json` declares a custom provider for it.
Off by default (a ~62 MB download, ~190 MB on disk). Turn it on with:

```yaml
paseo_install_kimicode: true
paseo_kimicode_version: "2.7.3"         # pin exactly; unpinned hosts drift apart
```

Install is Moonshot's **official installer** (`code.kimi.com/kimi-code/install.sh`): one glibc binary
at `~/.kimi-code/bin/kimi`, sha256-verified against the release manifest, no Node at runtime. Needs
`bash`, `curl`, `sha256sum` and outbound HTTPS; musl is unsupported. The role passes
`KIMI_NO_MODIFY_PATH`, because left alone the installer **edits the user's shell rc** and renames any
older `kimi` it finds on `PATH`.

The download host also sets the region Kimi assumes until first login: `code.kimi.com` →
`mainland-cn`, `code.kimi.ai` → `global`. It only matters for the OAuth login. For the global
channel set `paseo_kimicode_install_url: "https://code.kimi.ai/kimi-code/install.sh"`.

### Credentials — the part that is easy to get wrong

**Kimi Code does not read provider credentials from the environment.** `KIMI_API_KEY=… kimi`,
`MOONSHOT_API_KEY`, `ANTHROPIC_API_KEY` — none of them reach a provider; Kimi's own docs say so
explicitly. Putting one in the daemon env file does nothing, silently. Three things do work on a
headless host:

1. **The `KIMI_MODEL_*` family** — the only env-only path. `KIMI_MODEL_NAME` is the switch; with it
   set, Kimi builds one in-memory provider from the rest. No file on the host, and it fits the
   vault → `/etc/paseo/paseo.env` pipeline as is:

   ```yaml
   paseo_kimicode_secrets:                 # from Vault
     KIMI_MODEL_NAME: "kimi-for-coding"    # the model id your endpoint serves
     KIMI_MODEL_API_KEY: "{{ vault_kimi_api_key }}"
     KIMI_MODEL_BASE_URL: "https://api.kimi.com/coding/v1"   # omitted: https://api.moonshot.ai/v1
   ```

2. **A `config.toml` provider with `api_key_env`.** Kimi re-reads the named variable on every
   request, so the file itself stays secret-free — the same split as `opencode.jsonc`. Use this for
   more than one model or provider:

   ```toml
   # kimi-config.toml, on the controller
   default_model = "kimi"
   [providers.kimi]
   type = "kimi"
   base_url = "https://api.kimi.com/coding/v1"
   api_key_env = "MY_KIMI_KEY"
   [models.kimi]
   provider = "kimi"
   model = "kimi-for-coding"
   max_context_size = 262144
   ```
   ```yaml
   paseo_kimicode_config_src: "{{ playbook_dir }}/kimi-config.toml"
   paseo_kimicode_secrets: { MY_KIMI_KEY: "{{ vault_kimi_api_key }}" }
   ```

3. **OAuth** (a Kimi Code subscription) — once per host, by hand. It is a device-code flow, so it
   works over SSH:

   ```sh
   sudo -u paseo env HOME=/var/lib/paseo /var/lib/paseo/.kimi-code/bin/kimi login
   ```

   The token lands in `~/.kimi-code/credentials`. That is per-host **state**, in the same category
   as `daemon-keypair.json`: the role never reads, writes or copies it.

### `config.toml` is closer to state than config

Kimi rewrites `~/.kimi-code/config.toml` itself: `kimi login` injects the OAuth provider reference
there, and model refreshes update the model entries. So the role leaves it alone by default, and
`paseo_kimicode_config_src` only **seeds** it — written when absent, never again.
`paseo_kimicode_config_overwrite: true` converges it on every play instead; don't combine that with
OAuth, since each overwrite drops the reference `kimi login` wrote and logs the host out.

### How Paseo finds it

The role writes this to `config.json` — the same shape Paseo's own "add provider" catalog produces,
with the binary path made absolute:

```json
"agents": { "providers": { "kimi": {
  "extends": "acp",
  "label": "Kimi Code CLI",
  "description": "Moonshot AI's open-source terminal coding agent",
  "command": ["/var/lib/paseo/.kimi-code/bin/kimi", "acp"],
  "env": { "KIMI_CODE_NO_AUTO_UPDATE": "1", "KIMI_CODE_HOME": "/var/lib/paseo/.kimi-code" }
} } }
```

Every key is load-bearing, verified against the daemon's schema and registry and confirmed on a
running 0.17.4 daemon:

- **`extends: "acp"`** is what makes it a provider. With only `command`, the entry passes validation
  and is then skipped without a word — Kimi never appears. (An earlier version of this role did
  exactly that.)
- **`label`** is required once `extends` is present; without it the daemon rejects `config.json` and
  will not start.
- **`command`** is absolute, so availability never depends on `PATH`.
- **`env`** reaches only the spawned `kimi acp`. `KIMI_CODE_NO_AUTO_UPDATE` freezes the pinned
  version — otherwise Kimi's update preflight can install a newer build in the background.
- The provider id must be exactly **`kimi`**: Paseo selects its Kimi-specific ACP client by that id.

As with Claude Code, **auth is not part of availability**: a host with no working credential still
reports Kimi as *ready* and fails at the first prompt.

### A bad credential never fails the play

`paseo_verify_kimicode_credential` (default on) sends one `GET <base>/models` with the
`KIMI_MODEL_API_KEY` as a bearer and **reports** the result — `200` works, `401` is a loud warning,
anything else is "could not verify". It never fails the play, and it only runs for path 1 with a
`kimi`/`openai` provider type: paths 2 and 3 leave the role nothing it can probe, and it says so
rather than guessing.

### Check it

```sh
ansible-playbook -i inventory.yml test-kimicode.yml --limit <host>
ansible-playbook -i inventory.yml test-kimicode-verify.yml --limit <host>
```

The verify play asks the **running daemon** which providers it reports — the one check that catches
a registration the schema accepts but the registry ignores.

## Daemon config

Grab each host's `config.json` back to the controller with `paseo_fetch_daemon_config: true`.
Because that file holds the bcrypt password hash, it's **redacted by default** — the hash (and
anything that looks like a credential) is stripped on the host before the file moves, landing at
`<paseo_daemon_config_fetch_dir>/<host>/config.json`. Set `paseo_daemon_config_redact: false` to
pull the raw file (which then nests under the full remote path, `…/<host>/<remote/path>/config.json`).

Redaction is value-aware (any bcrypt-looking string is caught regardless of the key name), so it
still works even though the exact key Paseo stores the hash under is build-dependent. Standalone:

```sh
./redact-json.py /var/lib/paseo/.paseo/config.json          # sanitized copy to stdout
```



- The password task gates on a sha256 marker of the desired secret: green when unchanged, re-runs on
  rotation. The marker is non-reversible and every password-touching task is `no_log`.
- On the first run, `set-password` writes `config.json` in its own format; the config merge then
  normalises it to canonical JSON (preserving the hash) in the same run. Subsequent runs are stable.
- Changing `hostnames`/`listen` later re-merges cleanly without clobbering the password.
- **Dry runs.** `--check` works on a host that has been provisioned at least once — the read-only
  probes (Node version, the CLI's foreground sub-command, the Kimi version) run for real, so the
  preview is truthful — and is the sensible first step before upgrading a fleet. On a brand-new host
  it stops at the first task that must run as the not-yet-created `paseo` user; that is inherent to
  a dry run, not a fault.

## Notes from the runbook worth keeping in mind

- `schedule create --host` requires `--cwd`; remote `paseo run`/`schedule` don't inherit a shell's
  working dir. Bake `--cwd` into any automation you build on top.
- On OpenRC, `daemon start` launches Paseo's own supervisor, which restarts a crashed worker by
  itself. What the delegation wrapper does not give you is a restart of that supervisor if *it*
  dies. For that, point `supervise-daemon` at Paseo's foreground mode (`daemon run` on
  `@getpaseo/cli` ≥ 0.9.0) with `/api/health` as the liveness probe.
