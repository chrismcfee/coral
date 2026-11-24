#!/usr/bin/env python3
"""Prove that a language server actually works, by speaking LSP to it.

Starts the server on stdio, completes the `initialize` handshake and — optionally — opens a file
and waits for what a real editor would get back: a diagnostic, a go-to-definition target, a hover.
Prints ONE JSON object on stdout describing what happened.

  lsp-probe.py [options] -- <server command> [args...]

  lsp-probe.py -- pyright-langserver --stdio                       # handshake only
  lsp-probe.py --root proj --open proj/app.py \\
      --expect-diagnostic 'assign' -- pyright-langserver --stdio     # a real type error comes back
  lsp-probe.py --root crate --open crate/src/main.rs \\
      --definition 2:12 --expect-definition 'library/alloc/' -- rust-analyzer

  --root DIR                workspace root sent to the server (default: a fresh empty directory,
                            removed afterwards — never the current one, which a server may crawl)
  --user NAME               run the SERVER as this user (the probe itself must be root). Proves
                            the install is usable by an ordinary account, not just by root
  --home DIR                HOME for the server (default with --user: the workspace root)
  --open FILE               open this file after the handshake
  --language-id ID          LSP languageId for --open (default: from the extension)
  --expect-diagnostic RE    wait for a diagnostic on FILE whose "<code> <message>" matches RE
  --definition LINE:COL     ask for the definition at this position (1-based)
  --expect-definition RE    ...and wait until a returned location's path matches RE
  --hover LINE:COL          ask for hover text at this position (1-based)
  --expect-hover RE         ...and wait until it matches RE
  --init JSON               initializationOptions to send
  --timeout SECONDS         overall budget (default 60)

Stdlib only, Python 3.8+ (--user needs 3.9+): it runs on the target with whatever Python Ansible
uses there.

Exit 0 when the handshake and every expectation succeeded, 1 otherwise, 2 on bad arguments.
"""
import argparse
import collections
import json
import os
import pwd
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, unquote, urlparse

LANGUAGE_IDS = {".py": "python", ".pyi": "python", ".rs": "rust"}

# What a minimal editor would advertise. Deliberately NO pull-diagnostics capability: without it
# every server pushes textDocument/publishDiagnostics, which is one code path to wait on instead
# of two. Work-done progress is off so servers don't ask us to create progress tokens.
CLIENT_CAPABILITIES = {
    "general": {"positionEncodings": ["utf-16"]},
    "textDocument": {
        "synchronization": {"didSave": True},
        "publishDiagnostics": {"relatedInformation": False},
        "hover": {"contentFormat": ["plaintext", "markdown"]},
        "definition": {"linkSupport": False},
    },
    "workspace": {"configuration": True, "workspaceFolders": True},
    "window": {"workDoneProgress": False},
    # rust-analyzer reports "can't load the standard library" and friends through this.
    "experimental": {"serverStatusNotification": True},
}


class ProbeError(Exception):
    pass


def uri_of(path):
    return "file://" + quote(os.path.realpath(path))


def path_of(uri):
    parsed = urlparse(uri)
    return os.path.realpath(unquote(parsed.path)) if parsed.scheme == "file" else uri


def position(text):
    """'LINE:COL' (1-based, as an editor shows it) -> LSP position (0-based)."""
    try:
        line, col = (int(part) for part in text.split(":", 1))
    except ValueError:
        raise argparse.ArgumentTypeError("expected LINE:COL, e.g. 3:14")
    if line < 1 or col < 1:
        raise argparse.ArgumentTypeError("LINE and COL are 1-based")
    return {"line": line - 1, "character": col - 1}


class Server:
    """A language server on stdio, with the JSON-RPC framing handled on background threads."""

    def __init__(self, argv, cwd, env, identity):
        try:
            # Its own session, so the whole tree (cargo, rustc, node workers) can be signalled.
            self.proc = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True, **identity)
        except OSError as exc:
            raise ProbeError("cannot start %r: %s" % (argv[0], exc))
        self.inbox = queue.Queue()
        self.stderr_tail = collections.deque(maxlen=25)
        self.next_id = 0
        self.responses = {}
        self.diagnostics = {}          # real path -> latest diagnostics list
        self.status = None             # last experimental/serverStatus
        self.messages = collections.deque(maxlen=8)   # window/showMessage + logMessage
        for target in (self._read_stdout, self._read_stderr):
            threading.Thread(target=target, daemon=True).start()

    def _read_stdout(self):
        stream = self.proc.stdout
        try:
            while True:
                length = None
                while True:
                    line = stream.readline()
                    if not line:
                        raise EOFError("server closed its output")
                    line = line.strip()
                    if not line:
                        break
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value.strip())
                if length is None:
                    continue
                body = b""
                while len(body) < length:
                    chunk = stream.read(length - len(body))
                    if not chunk:
                        raise EOFError("server closed its output mid-message")
                    body += chunk
                self.inbox.put(json.loads(body.decode("utf-8")))
        except Exception as exc:       # noqa: BLE001 - any failure here ends the conversation
            self.inbox.put({"__closed__": str(exc)})

    def _read_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    def _send(self, message):
        data = json.dumps(message).encode("utf-8")
        try:
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
            self.proc.stdin.flush()
        except OSError as exc:
            raise ProbeError("server stopped reading its input: %s" % exc)

    def request(self, method, params):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        return self.next_id

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _handle(self, message, folders):
        if "__closed__" in message:
            code = self.proc.poll()
            raise ProbeError("server exited%s: %s" % (
                "" if code is None else " with status %d" % code, message["__closed__"]))
        method = message.get("method")
        if method is None:                                  # a response to one of our requests
            self.responses[message.get("id")] = message
            return
        params = message.get("params") or {}
        if "id" in message:                                 # the server is asking US something
            if method == "workspace/configuration":
                result = [None] * len(params.get("items", []))   # "no settings": use defaults
            elif method == "workspace/workspaceFolders":
                result = folders
            else:                                           # registerCapability, progress, ...
                result = None
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        elif method == "textDocument/publishDiagnostics":
            self.diagnostics[path_of(params.get("uri", ""))] = params.get("diagnostics", [])
        elif method == "experimental/serverStatus":
            self.status = params
        elif method in ("window/showMessage", "window/logMessage"):
            self.messages.append(str(params.get("message", ""))[:300])

    def pump(self, done, deadline, folders, step=0.2):
        """Process incoming messages until done() is truthy; False if the deadline passed first."""
        while True:
            if done():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                self._handle(self.inbox.get(timeout=min(step, remaining)), folders)
            except queue.Empty:
                pass

    def call(self, method, params, deadline, folders):
        """Send a request and wait for its response; returns the response message or None."""
        rid = self.request(method, params)
        if not self.pump(lambda: rid in self.responses, deadline, folders):
            return None
        return self.responses.pop(rid)

    def stop(self, folders):
        """Polite shutdown, then make sure nothing is left running."""
        try:
            if self.proc.poll() is None:
                self.call("shutdown", None, time.monotonic() + 5, folders)
                self.notify("exit", None)
                self.proc.wait(timeout=3)
        except (ProbeError, subprocess.TimeoutExpired, OSError):
            pass
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except OSError:
                break
            try:
                self.proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue


def diagnostic_text(diag):
    code = diag.get("code", "")
    if isinstance(code, dict):
        code = code.get("value", "")
    return ("%s %s" % (code, diag.get("message", ""))).strip()


def locations(result):
    """Normalise a definition result (Location | Location[] | LocationLink[] | null) to paths."""
    if not result:
        return []
    items = result if isinstance(result, list) else [result]
    return [path_of(item.get("uri") or item.get("targetUri") or "") for item in items]


def hover_text(result):
    contents = (result or {}).get("contents", "")
    parts = contents if isinstance(contents, list) else [contents]
    return "\n".join(p.get("value", "") if isinstance(p, dict) else str(p) for p in parts)


def poll_request(server, method, params, extract, pattern, deadline, folders):
    """Re-ask until the answer matches: a server answers 'nothing yet' while it is still loading."""
    last = None
    while time.monotonic() < deadline:
        response = server.call(method, params, deadline, folders)
        if response is None:
            break
        if "error" not in response:
            last = extract(response.get("result"))
            haystack = last if isinstance(last, list) else [last]
            if any(pattern.search(item or "") for item in haystack):
                return True, last
        server.pump(lambda: False, min(deadline, time.monotonic() + 1.0), folders)
    return False, last


def identity_of(name):
    """Popen keywords that make the child run as `name`, with no supplementary groups."""
    if sys.version_info < (3, 9):
        raise ProbeError("--user needs Python 3.9 or newer on the target")
    if os.geteuid() != 0:
        raise ProbeError("--user needs the probe itself to run as root")
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise ProbeError("no such user: %s" % name)
    return entry, {"user": entry.pw_uid, "group": entry.pw_gid, "extra_groups": []}


def run(args, report):
    entry, identity = identity_of(args.user) if args.user else (None, {})
    scratch = None
    if args.root:
        root = os.path.realpath(args.root)
    else:
        scratch = root = os.path.realpath(tempfile.mkdtemp(prefix="lsp-probe."))
        if entry:
            os.chown(root, entry.pw_uid, entry.pw_gid)
    env = dict(os.environ)
    if entry:
        # sudo leaves HOME pointing at root's home, which the server's user cannot even read.
        env.update(HOME=root, USER=entry.pw_name, LOGNAME=entry.pw_name)
    if args.home:
        env["HOME"] = os.path.realpath(args.home)
    folders = [{"uri": uri_of(root), "name": os.path.basename(root) or "root"}]
    deadline = time.monotonic() + args.timeout
    try:
        server = Server(args.command, root, env, identity)
    except ProbeError:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        raise
    try:
        response = server.call("initialize", {
            "processId": os.getpid(),
            "clientInfo": {"name": "lsp-probe", "version": "1"},
            "rootUri": folders[0]["uri"],
            "rootPath": root,
            "workspaceFolders": folders,
            "capabilities": CLIENT_CAPABILITIES,
            "initializationOptions": args.init,
        }, deadline, folders)
        if response is None:
            raise ProbeError("no answer to 'initialize' within %ss" % args.timeout)
        if "error" in response:
            raise ProbeError("'initialize' was rejected: %s" % response["error"].get("message"))
        result = response.get("result") or {}
        report["server"] = result.get("serverInfo") or {}
        report["capabilities"] = sorted(result.get("capabilities") or {})
        if "capabilities" not in result:
            raise ProbeError("'initialize' answered without capabilities")
        server.notify("initialized", {})

        if args.open:
            path = os.path.realpath(args.open)
            suffix = os.path.splitext(path)[1].lower()
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            document = {"uri": uri_of(path)}
            server.notify("textDocument/didOpen", {"textDocument": dict(
                document, languageId=args.language_id or LANGUAGE_IDS.get(suffix, "plaintext"),
                version=1, text=text)})

            if args.expect_diagnostic:
                wanted = re.compile(args.expect_diagnostic)
                matched = server.pump(
                    lambda: any(wanted.search(diagnostic_text(d))
                                for d in server.diagnostics.get(path, [])),
                    deadline, folders)
                report["diagnostics"] = [
                    {"line": d.get("range", {}).get("start", {}).get("line", -1) + 1,
                     "text": diagnostic_text(d)[:200]}
                    for d in server.diagnostics.get(path, [])][:12]
                if not matched:
                    raise ProbeError("no diagnostic matching /%s/ within %ss"
                                     % (args.expect_diagnostic, args.timeout))

            if args.definition:
                matched, found = poll_request(
                    server, "textDocument/definition",
                    {"textDocument": document, "position": args.definition},
                    locations, re.compile(args.expect_definition or "."), deadline, folders)
                report["definition"] = found or []
                if not matched:
                    raise ProbeError("no definition matching /%s/ within %ss"
                                     % (args.expect_definition or ".", args.timeout))

            if args.hover:
                matched, found = poll_request(
                    server, "textDocument/hover",
                    {"textDocument": document, "position": args.hover},
                    hover_text, re.compile(args.expect_hover or r"\S"), deadline, folders)
                report["hover"] = (found or "")[:300]
                if not matched:
                    raise ProbeError("no hover matching /%s/ within %ss"
                                     % (args.expect_hover or r"\S", args.timeout))
        report["ok"] = True
    finally:
        if server.status:
            report["status"] = {k: server.status.get(k) for k in ("health", "quiescent", "message")}
        if not report["ok"]:
            report["stderr"] = list(server.stderr_tail)
            report["messages"] = list(server.messages)
        server.stop(folders)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="Prove a language server works by speaking LSP to it.",
        usage="%(prog)s [options] -- <server command> [args...]")
    parser.add_argument("--root")
    parser.add_argument("--user")
    parser.add_argument("--home")
    parser.add_argument("--open")
    parser.add_argument("--language-id")
    parser.add_argument("--expect-diagnostic")
    parser.add_argument("--definition", type=position)
    parser.add_argument("--expect-definition")
    parser.add_argument("--hover", type=position)
    parser.add_argument("--expect-hover")
    parser.add_argument("--init", type=json.loads, default=None)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("no server command given (put it after --)")
    if (args.expect_diagnostic or args.definition or args.hover) and not args.open:
        parser.error("--expect-diagnostic / --definition / --hover need --open FILE")

    report = {"ok": False, "command": args.command,
              "user": args.user or pwd.getpwuid(os.geteuid()).pw_name}
    started = time.monotonic()
    try:
        run(args, report)
    except ProbeError as exc:
        report["error"] = str(exc)
    except OSError as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
    report["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
