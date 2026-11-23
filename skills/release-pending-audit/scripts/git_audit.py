#!/usr/bin/env python3
"""Fetch immutable Git snapshots and check release ancestry; never write to Jira."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class AuditError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_error(message):
    message = re.sub(r"(\w+://)[^\s/@]+@", r"\1[redacted]@", str(message))
    message = re.sub(r"([?&](?:token|access_token|password|key)=)[^&\s]+",
                     r"\1[redacted]", message, flags=re.I)
    return message.strip()[:600]


def git(path, *args, timeout=180, check=True):
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_NAMESPACE", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE"):
        env.pop(name, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    command = ["git", "--no-replace-objects", "-c", f"core.hooksPath={os.devnull}",
               "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-C", str(path), *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise AuditError(f"Git {args[0]} timed out; outcome must be rechecked") from exc
    except OSError as exc:
        raise AuditError(f"Cannot execute Git: {safe_error(exc)}") from exc
    if check and result.returncode:
        raise AuditError(f"Git {args[0]} failed: {safe_error(result.stderr)}")
    return result


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"Missing or invalid {label}")
    return value


def full_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise AuditError("Expected a full 40- or 64-digit commit SHA")
    return value.lower()


def web_url(value):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        return bool(parsed and parsed.scheme in ("https", "http") and parsed.hostname
                    and not parsed.username and not parsed.password)
    except ValueError:
        return False


def repo_identity(url, base=None):
    """Normalize transport, not ownership; do not expose embedded credentials."""
    url = text(url, "remote URL").strip()
    if "://" in url:
        parsed = urlsplit(url)
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            return Path(unquote(parsed.path)).resolve().as_uri()
        if parsed.scheme not in ("https", "http", "ssh", "git") or not parsed.hostname:
            raise AuditError("Unsupported remote URL; confirm repository identity")
        host, path = parsed.hostname.lower(), unquote(parsed.path)
        try:
            port = parsed.port
        except ValueError as exc:
            raise AuditError("Invalid remote port") from exc
        defaults = {"https": 443, "http": 80, "ssh": 22, "git": 9418}
        if port and port != defaults[parsed.scheme]:
            host += f":{port}"
    else:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):([^:].*)", url)
        if match:
            host, path = match.group(1).lower(), match.group(2)
        elif url.startswith(("/", "./", "../", "~")):
            local = Path(url).expanduser()
            return ((Path(base or Path.cwd()) / local).resolve()).as_uri()
        else:
            raise AuditError("Unsupported remote URL; confirm repository identity")
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(p in ("", ".", "..") for p in path.split("/")) or re.search(r"\s", path):
        raise AuditError("Invalid remote repository path")
    if host == "github.com":
        path = path.lower()
    return f"{host}/{path}"


def checkout(path, timeout):
    path = Path(path).expanduser().resolve()
    if not (path / ".git").exists():
        raise AuditError(f"Local checkout missing: {path}")
    root = git(path, "rev-parse", "--show-toplevel", timeout=timeout).stdout.strip()
    if Path(root).resolve() != path:
        raise AuditError("Expected the repository root, not a subdirectory")
    return path


def complete_history(path, timeout, allow_shallow=False):
    shallow = git(path, "rev-parse", "--is-shallow-repository", timeout=timeout).stdout.strip()
    if shallow != "false" and not allow_shallow:
        raise AuditError("Shallow history; a successful full fetch is required")
    partial = git(path, "config", "--get", "extensions.partialClone",
                  timeout=timeout, check=False)
    promisor = git(path, "config", "--bool", "--get-regexp", r"^remote\..*\.promisor$",
                   timeout=timeout, check=False)
    if partial.returncode not in (0, 1) or promisor.returncode not in (0, 1):
        raise AuditError("Cannot determine whether the repository has complete history")
    if partial.returncode == 0 or any(line.endswith(" true") for line in promisor.stdout.splitlines()):
        raise AuditError("Partial/promisor clone; use a full clone for offline proof")
    graft = Path(git(path, "rev-parse", "--git-path", "info/grafts", timeout=timeout).stdout.strip())
    if not graft.is_absolute():
        graft = path / graft
    if graft.exists() and graft.stat().st_size:
        raise AuditError("Local grafts alter history; cannot verify original ancestry")
    return shallow == "true"


def inventory(workspace, timeout=180):
    workspace = Path(workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise AuditError(f"Workspace does not exist: {workspace}")
    roots = [workspace] + sorted(p for p in workspace.iterdir()
                                 if p.is_dir() and not p.name.startswith("."))
    repositories, errors, seen = [], [], set()
    for root in roots:
        if not (root / ".git").exists() or root.resolve() in seen:
            continue
        seen.add(root.resolve())
        try:
            root = checkout(root, timeout)
            remotes = []
            for remote in git(root, "remote", timeout=timeout).stdout.splitlines():
                try:
                    url = git(root, "remote", "get-url", "--", remote, timeout=timeout).stdout.strip()
                    remotes.append({"name": remote, "identity": repo_identity(url, root)})
                except AuditError as exc:
                    remotes.append({"name": remote, "error": safe_error(exc)})
            repositories.append({"path": str(root), "remotes": remotes})
        except AuditError as exc:
            errors.append({"path": str(root), "error": safe_error(exc)})
    return {"version": 1, "repositories": repositories, "errors": errors}


def sync_one(path, remote, expected, branch, timeout):
    path = checkout(path, timeout)
    git(path, "check-ref-format", f"refs/remotes/{remote}/probe", timeout=timeout)
    url = git(path, "remote", "get-url", "--", remote, timeout=timeout).stdout.strip()
    actual = repo_identity(url, path)
    if actual != expected:
        raise AuditError(f"Remote identity mismatch: expected {expected}, found {actual}")
    shallow = complete_history(path, timeout, allow_shallow=True)
    if not branch:
        advertised = git(path, "ls-remote", "--symref", "--", remote, "HEAD", timeout=timeout).stdout
        branches = re.findall(r"^ref: refs/heads/(.+)\tHEAD$", advertised, re.M)
        if len(branches) != 1:
            raise AuditError("Remote default branch is unresolved; confirm host metadata")
        branch = branches[0]
    git(path, "check-ref-format", f"refs/heads/{branch}", timeout=timeout)
    options = ["fetch", "--atomic", "--quiet", "--prune", "--no-tags",
               "--no-recurse-submodules", "--no-write-fetch-head"]
    if shallow:
        options.append("--unshallow")
    git(path, *options, "--", remote, f"+refs/heads/*:refs/remotes/{remote}/*", timeout=timeout)
    complete_history(path, timeout)
    sha = git(path, "rev-parse", "--verify", "--end-of-options",
              f"refs/remotes/{remote}/{branch}^{{commit}}", timeout=timeout).stdout.strip()
    return {"path": str(path), "remote": remote, "identity": actual,
            "default_branch": branch, "default_sha": full_sha(sha), "fetched_at": now()}


def sync_repositories(document, timeout=180):
    specs = document.get("repositories")
    if not isinstance(specs, list):
        raise AuditError("Sync input must contain a repositories list")
    groups, results, seen = {}, {}, set()
    for spec in specs:
        if not isinstance(spec, dict):
            raise AuditError("Repository requests must be objects")
        name = text(spec.get("id"), "repository id")
        if name in seen:
            raise AuditError(f"Duplicate repository id: {name}")
        seen.add(name)
        try:
            path = Path(text(spec.get("path"), "repository path")).expanduser().resolve()
            remote = text(spec.get("remote", "origin"), "remote name")
            expected = text(spec.get("expected_identity"), "expected repository identity")
            branch = spec.get("default_branch")
            if branch is not None:
                text(branch, "default branch")
            groups.setdefault((path, remote), []).append((name, expected, branch))
        except AuditError as exc:
            results[name] = {"error": safe_error(exc)}
    for (path, remote), aliases in groups.items():
        try:
            identities = {entry[1] for entry in aliases}
            branches = {entry[2] for entry in aliases if entry[2] is not None}
            if len(identities) != 1 or len(branches) > 1:
                raise AuditError("Conflicting identities/default branches for the same checkout and remote")
            snapshot = sync_one(path, remote, next(iter(identities)),
                                next(iter(branches), None), timeout)
        except (AuditError, OSError) as exc:
            snapshot = {"path": str(path), "remote": remote, "error": safe_error(exc)}
        for name, _, _ in aliases:
            results[name] = dict(snapshot)
    return {"version": 1, "created_at": now(), "repositories": results}


class History:
    """Memoized offline reads shared across all PRs, targets, and tickets."""

    def __init__(self, snapshots, timeout=180):
        self.snapshots = snapshots.get("repositories")
        if not isinstance(self.snapshots, dict):
            raise AuditError("Snapshots must contain a repositories object")
        self.timeout = timeout
        self.repositories, self.commits, self.ancestry, self.revert_cache = {}, {}, {}, {}

    def repository(self, name):
        text(name, "repository id")
        if name not in self.repositories:
            try:
                snapshot = self.snapshots.get(name)
                if not isinstance(snapshot, dict) or snapshot.get("error"):
                    detail = snapshot.get("error", "snapshot missing") if isinstance(snapshot, dict) else "snapshot missing"
                    raise AuditError(f"{name}: {detail}")
                text(snapshot.get("fetched_at"), "snapshot fetch timestamp")
                path = checkout(text(snapshot.get("path"), "snapshot path"), self.timeout)
                complete_history(path, self.timeout)
                remote = text(snapshot.get("remote"), "snapshot remote")
                url = git(path, "remote", "get-url", "--", remote, timeout=self.timeout).stdout.strip()
                if repo_identity(url, path) != snapshot.get("identity"):
                    raise AuditError(f"{name}: remote identity changed since snapshot")
                sha = full_sha(snapshot.get("default_sha"))
                self._commit(path, sha)
                self.repositories[name] = (path, sha)
            except (AuditError, OSError) as exc:
                self.repositories[name] = AuditError(safe_error(exc))
        value = self.repositories[name]
        if isinstance(value, AuditError):
            raise value
        return value

    def _commit(self, path, sha):
        key = (path, sha)
        if key not in self.commits:
            result = git(path, "cat-file", "-t", sha, timeout=self.timeout, check=False)
            self.commits[key] = result.returncode == 0 and result.stdout.strip() == "commit"
        if not self.commits[key]:
            raise AuditError(f"Commit unavailable or not a commit: {sha}")

    def ancestor(self, repository, older, newer):
        path, _ = self.repository(repository)
        older, newer = full_sha(older), full_sha(newer)
        self._commit(path, older)
        self._commit(path, newer)
        key = (path, older, newer)
        if key not in self.ancestry:
            result = git(path, "merge-base", "--is-ancestor", older, newer,
                         timeout=self.timeout, check=False)
            if result.returncode not in (0, 1):
                raise AuditError(f"Ancestry check failed: {safe_error(result.stderr)}")
            self.ancestry[key] = result.returncode == 0
        return self.ancestry[key]

    def reverts(self, repository, landed, production):
        path, _ = self.repository(repository)
        landed, production = full_sha(landed), full_sha(production)
        key = (path, landed, production)
        if key not in self.revert_cache:
            result = git(path, "log", "--format=%H", "--no-notes", "--no-show-signature",
                         "--fixed-strings", "--regexp-ignore-case",
                         f"--grep=This reverts commit {landed}", f"{landed}..{production}", "--",
                         timeout=self.timeout)
            self.revert_cache[key] = result.stdout.splitlines()
        return self.revert_cache[key]


def check_requirement(requirement, history, blockers, missing, evidence):
    if not isinstance(requirement, dict):
        blockers.append("Invalid PR/MR requirement")
        return
    link = requirement.get("url")
    if not web_url(link):
        blockers.append("Missing or invalid PR/MR evidence URL")
    if requirement.get("merged") is False:
        missing.append(f"Required PR/MR is not merged: {link}")
        return
    if requirement.get("merged") is not True:
        blockers.append(f"PR/MR merge state is unconfirmed: {link}")
        return
    if requirement.get("targets_complete") is not True:
        blockers.append("Production target mapping is incomplete")
    landed = requirement.get("landed_commits")
    targets = requirement.get("targets")
    if not isinstance(landed, list) or not landed:
        blockers.append("Integrated commit(s) are unresolved")
        return
    if not isinstance(targets, list) or not targets:
        blockers.append("No production refs mapped")
        return
    repository = requirement.get("repository")
    try:
        _, default_sha = history.repository(repository)
        landed = list(dict.fromkeys(full_sha(sha) for sha in landed))
        for sha in landed:
            if not history.ancestor(repository, sha, default_sha):
                raise AuditError(f"Integrated commit {sha[:12]} is not on the fetched default branch")
    except AuditError as exc:
        blockers.append(safe_error(exc))
        return
    seen = set()
    for target in targets:
        try:
            if not isinstance(target, dict):
                raise AuditError("Invalid production target")
            name = text(target.get("name"), "production target name")
            if name in seen:
                raise AuditError(f"Duplicate production target: {name}")
            seen.add(name)
            source = text(target.get("source"), "app-interface source file/key")
            prod_sha = full_sha(target.get("sha"))
            for sha in landed:
                included = history.ancestor(repository, sha, prod_sha)
                reverts = history.reverts(repository, sha, prod_sha) if included else []
                evidence.append({"pr": link, "repository": repository, "target": name,
                                 "source": source, "landed_sha": sha, "production_sha": prod_sha,
                                 "included": included, "possible_reverts": reverts})
                if not included:
                    missing.append(f"{name}: production does not include {sha[:12]}")
                if reverts:
                    blockers.append(f"{name}: possible revert of {sha[:12]}; confirm reapplication")
        except AuditError as exc:
            blockers.append(safe_error(exc))


def check_tickets(document, snapshots, timeout=180):
    history = History(snapshots, timeout)
    tickets = document.get("tickets")
    if not isinstance(tickets, list):
        raise AuditError("Check input must contain a tickets list")
    global_blockers = []
    try:
        config_sha = full_sha(document.get("app_interface_sha"))
        _, observed_sha = history.repository(document.get("app_interface_repository"))
        if config_sha != observed_sha:
            raise AuditError("Reviewed app-interface SHA differs from its snapshot; remap production refs")
    except AuditError as exc:
        global_blockers.append(safe_error(exc))
    results, seen = [], set()
    for ticket in tickets:
        if not isinstance(ticket, dict):
            raise AuditError("Ticket entries must be objects")
        key = text(ticket.get("key"), "ticket key")
        if key in seen:
            raise AuditError(f"Duplicate ticket key: {key}")
        seen.add(key)
        blockers, missing, evidence = list(global_blockers), [], []
        if not web_url(ticket.get("url")):
            blockers.append("Missing or invalid ticket URL")
        if ticket.get("scope_complete") is not True:
            blockers.append("Ticket work/comment scope is incomplete")
        supplied = ticket.get("blockers", [])
        if not isinstance(supplied, list) or any(not isinstance(b, str) or not b.strip() for b in supplied):
            blockers.append("Invalid blocker list")
        else:
            blockers.extend(supplied)
        requirements = ticket.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            blockers.append("No required PRs/MRs identified")
        else:
            for requirement in requirements:
                check_requirement(requirement, history, blockers, missing, evidence)
        status = "Needs clarification" if blockers else "Not released" if missing else "Released"
        results.append({"key": key, "url": ticket.get("url"), "status": status,
                        "reasons": list(dict.fromkeys(blockers + missing)), "checks": evidence})
    return {"version": 1, "checked_at": now(), "basis": "app-interface configured production refs; not live rollout",
            "app_interface_sha": document.get("app_interface_sha"), "tickets": results}


def load_json(path):
    with Path(path).expanduser().open(encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise AuditError("Input JSON must be an object")
    return document


def save_json(path, document):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=180, help="timeout per Git command in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inv = subparsers.add_parser("inventory", help="list local repositories/remotes; no network")
    inv.add_argument("workspace")
    sync = subparsers.add_parser("sync", help="fetch grouped repositories and pin default-branch snapshots")
    sync.add_argument("input")
    check = subparsers.add_parser("check", help="check all ticket ancestry offline; no Jira access")
    check.add_argument("input")
    check.add_argument("--snapshots", required=True)
    for command in (inv, sync, check):
        command.add_argument("--output", help="save full JSON privately; print a compact check summary")
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise AuditError("Timeout must be positive")
        if args.command == "inventory":
            result = inventory(args.workspace, args.timeout)
        elif args.command == "sync":
            result = sync_repositories(load_json(args.input), args.timeout)
        else:
            result = check_tickets(load_json(args.input), load_json(args.snapshots), args.timeout)
        printed = result
        if args.output:
            output = save_json(args.output, result)
            if args.command == "check":
                printed = {"output": output, "tickets": [
                    {"key": row["key"], "url": row["url"], "status": row["status"],
                     "reason": next(iter(row["reasons"]), "")}
                    for row in result["tickets"]]}
        print(json.dumps(printed, indent=2))
        return int(args.command == "sync" and any("error" in row for row in result["repositories"].values()))
    except (AuditError, OSError, ValueError) as exc:
        print(json.dumps({"error": safe_error(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
