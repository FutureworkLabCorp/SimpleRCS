"""Local-filesystem `WikiStorage` implementation (GPC-186).

Layout convention (GPC-185 — tentative, see README note below):

    <root>/foo/bar.md                    <- live page content, plain file
    <root>/<history_dirname>/foo/bar.md.srcs   <- SimpleRCS history for that page

The history tree mirrors the page tree under a single hidden directory
(default `.srcs`) instead of `tools/srcs_diff.py`'s co-located
`bar.md.srcs` convention, so `root` stays a clean directory of nothing but
`.md` files — friendly to being opened directly in an editor, synced via
Dropbox/JuiceFS, etc. This is a POC-time decision, not yet confirmed with
the team (GPC-185).

Delete semantics: `delete_page()` removes the live `.md` file but leaves its
`.srcs` history in place, with one last tombstone commit (`log="[deleted]"`)
appended. This is a deliberate soft-delete: nothing in a reverse-delta
history should be silently destroyed, and it leaves the door open for a
future "restore" endpoint that just re-commits an old `checkout()`.

Concurrency: a per-page `threading.Lock` (held only for the process
lifetime of one `LocalFileStorage` instance — see `wiki_api.dependencies`)
serializes *all* access to a given page — reads included, not just writes —
across threadpool threads. Earlier versions of this file only locked
`write_page`/`delete_page`, which let a concurrent read observe SimpleRCS's
`.srcs` file mid-mutation (it rewrites in place via seek+write+truncate, not
an atomic rename) and return corrupted content; every method that opens a
page's `.srcs` file now takes the same lock. This buys correctness, not
throughput — two reads of the *same* page are serialized too — which is an
acceptable trade for a POC. It is still not a substitute for the proper
concurrency design tracked in GPC-189 (it does nothing across multiple
worker processes, since the lock table is in-process memory).

Known, deliberately-not-fixed-here limitations (proportionate to POC scope):
- Case-insensitive-but-case-preserving filesystems (macOS APFS default,
  Windows) let two differently-cased paths (`notes/a.md` vs `Notes/A.md`)
  alias to the same file; an unconditional write under the second spelling
  silently overwrites the first. Fixing this generally means deciding on a
  normalization policy (reject vs. case-fold) — a real design question, not
  a quick patch, so it's left open rather than guessed at here.
- `get_history`/`get_version`/`diff_versions` scan a page's full commit log
  to validate a requested version exists; for pages with very long history
  this is O(history length) per request. No caching/indexing yet.
"""

import logging
import threading
import uuid
from pathlib import Path

from simple_rcs.simple_rcs import SimpleRCS
from wiki_api.storage.base import HistoryEntry, PageContent, PageInfo, WikiStorage
from wiki_api.storage.exceptions import (
    InvalidPagePathError,
    PageConflictError,
    PageNotFoundError,
    VersionNotFoundError,
)

logger = logging.getLogger(__name__)

# Headroom under common OS limits (POSIX NAME_MAX=255 bytes/segment,
# macOS PATH_MAX=1024) once the `.srcs` history tree doubles the nesting
# and appends its own suffix. Validating up front turns a would-be 500
# (OSError from the OS itself) into a clean 400.
_MAX_SEGMENT_BYTES = 200
_MAX_PATH_CHARS = 400


def _unquote_etag(value: str) -> str:
    """Strips RFC 7232 quoting (and a weak `W/` prefix) from an If-Match value.

    Our own `ETag` responses are now quoted (`"1.0"`), but this keeps
    unquoted input (simpler/older clients, this repo's own earlier tests)
    working too — both compare equal to the plain version string SimpleRCS
    hands back.
    """
    v = value.strip()
    if v.startswith(("W/", "w/")):
        v = v[2:]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


class LocalFileStorage(WikiStorage):
    def __init__(self, root: Path | str, history_dirname: str = ".srcs") -> None:
        self.root = Path(root)
        self.history_dirname = history_dirname
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- path handling --------------------------------------------------

    def _normalize_rel_path(self, path: str) -> str:
        """Validates and normalizes a client-supplied page path.

        Rejects anything that isn't a plain, relative `*.md` path: control
        characters, empty segments, `.`/`..` (traversal), leading-dot
        segments (reserved for `history_dirname`), and segments/paths long
        enough to trip OS-level filename limits. The final `resolve()` +
        `is_relative_to()` check is defense-in-depth against symlink
        escapes on top of the explicit segment checks above.
        """
        if not path:
            raise InvalidPagePathError(path, "path is empty")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
            # Catches embedded NUL/LF/etc. Two independent reasons to reject
            # these outright rather than let them fall through to the OS:
            # a NUL makes pathlib's resolve()/lstat() raise a bare, unmapped
            # ValueError (surfaces as a 500), and a '\n' makes SimpleRCS's
            # own path-vs-raw-content heuristic (it treats any string
            # containing '\n' as literal content, not a path — see
            # SimpleRCS.__init__) silently skip creating the history file.
            raise InvalidPagePathError(path, "path must not contain control characters")
        if len(path) > _MAX_PATH_CHARS:
            raise InvalidPagePathError(path, f"path is longer than {_MAX_PATH_CHARS} characters")
        if not path.endswith(".md"):
            raise InvalidPagePathError(path, "page paths must end with .md")

        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise InvalidPagePathError(path, "path must not contain empty, '.', or '..' segments")
        if any(part.startswith(".") for part in parts):
            raise InvalidPagePathError(
                path,
                f"path segments must not start with '.' (reserved for {self.history_dirname}/)",
            )
        if any(len(part.encode("utf-8")) > _MAX_SEGMENT_BYTES for part in parts):
            raise InvalidPagePathError(path, f"a path segment exceeds {_MAX_SEGMENT_BYTES} bytes")

        rel_path = "/".join(parts)
        root_resolved = self.root.resolve()
        candidate = (root_resolved / rel_path).resolve()
        if not candidate.is_relative_to(root_resolved):
            raise InvalidPagePathError(path, "path escapes the wiki root")
        return rel_path

    def _page_fs_path(self, rel_path: str) -> Path:
        return self.root / rel_path

    def _history_fs_path(self, rel_path: str) -> Path:
        return self.root / self.history_dirname / f"{rel_path}.srcs"

    def _lock_for(self, rel_path: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(rel_path, threading.Lock())

    # -- SimpleRCS helpers ------------------------------------------------

    @staticmethod
    def _close(rcs: SimpleRCS) -> None:
        """Explicitly closes the handle SimpleRCS opened for us.

        SimpleRCS only closes it in `__del__`; relying on GC timing for file
        handles is asking for FD exhaustion under load, so we close eagerly.
        """
        if rcs.owns_handle and not rcs.stream.closed:
            rcs.stream.close()

    def _head_meta(self, history_path: Path) -> dict:
        """Reads just the HEAD metadata. Caller must hold `_lock_for(rel_path)`."""
        if not history_path.exists():
            return {}
        rcs = SimpleRCS(str(history_path))
        try:
            entries = rcs.log(limit=1)
        finally:
            self._close(rcs)
        return entries[0] if entries else {}

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)  # atomic rename on POSIX filesystems

    # -- WikiStorage ------------------------------------------------------

    def list_pages(self) -> list[PageInfo]:
        if not self.root.exists():
            return []
        pages = []
        for md_path in sorted(self.root.rglob("*.md")):
            rel_path = md_path.relative_to(self.root).as_posix()
            if self.history_dirname in rel_path.split("/"):
                continue  # defensive; history lives under a name .md pages can't use anyway
            with self._lock_for(rel_path):
                meta = self._head_meta(self._history_fs_path(rel_path))
            pages.append(
                PageInfo(
                    path=rel_path,
                    version=meta.get("ver", ""),
                    author=meta.get("author", ""),
                    date=meta.get("date", ""),
                ),
            )
        return pages

    def page_exists(self, path: str) -> bool:
        rel_path = self._normalize_rel_path(path)
        return self._page_fs_path(rel_path).exists()

    def read_page(self, path: str) -> PageContent:
        rel_path = self._normalize_rel_path(path)
        page_path = self._page_fs_path(rel_path)
        history_path = self._history_fs_path(rel_path)

        with self._lock_for(rel_path):
            if not page_path.exists():
                raise PageNotFoundError(rel_path)

            # Read the live file directly rather than via SimpleRCS.checkout():
            # it's a real file on disk by design (GPC-185's "looks like a real
            # filesystem" goal), and HEAD reads are what SimpleRCS optimizes
            # for anyway, so this just skips a redundant file open. Version
            # metadata still comes from history, since the .md file has none.
            content = page_path.read_text(encoding="utf-8")
            meta = self._head_meta(history_path)

        return PageContent(
            path=rel_path,
            content=content,
            version=meta.get("ver", ""),
            author=meta.get("author", ""),
            date=meta.get("date", ""),
            message=meta.get("log", ""),
        )

    def write_page(
        self,
        path: str,
        content: str,
        *,
        author: str,
        message: str = "",
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> PageContent:
        rel_path = self._normalize_rel_path(path)
        history_path = self._history_fs_path(rel_path)
        page_path = self._page_fs_path(rel_path)

        with self._lock_for(rel_path):
            try:
                history_path.parent.mkdir(parents=True, exist_ok=True)
                rcs = SimpleRCS(str(history_path))
            except OSError as exc:
                # Most commonly: this path collides with an existing page
                # used as a directory prefix (e.g. 'foo.md' then
                # 'foo.md/bar.md'), or a segment/OS name-length limit was
                # hit despite _normalize_rel_path's own headroom check.
                raise InvalidPagePathError(rel_path, f"filesystem rejected this path: {exc}") from exc

            try:
                existing = rcs.log(limit=1)
                current_version = existing[0]["ver"] if existing else None

                # Create-only precondition: tied to the *live* page, not to
                # history presence. delete_page() intentionally leaves a
                # tombstone commit behind, so history presence alone would
                # wrongly treat a soft-deleted page as "still existing" and
                # block recreating it.
                if if_none_match == "*" and page_path.exists():
                    raise PageConflictError(rel_path, expected_version=None, current_version=current_version)
                if if_match is not None and _unquote_etag(if_match) != current_version:
                    raise PageConflictError(rel_path, expected_version=if_match, current_version=current_version)

                new_version = rcs.commit(content, author=author, log=message)
                # SimpleRCS may normalize content (e.g. appends a trailing
                # newline for text — see SimpleRCS.commit's EOL policy), so
                # read HEAD back rather than trusting the input we sent it:
                # the .md file on disk must match exactly what history says
                # version `new_version` is.
                stored_content = rcs.checkout()
                meta = rcs.log(limit=1)[0]
            finally:
                self._close(rcs)

            try:
                page_path.parent.mkdir(parents=True, exist_ok=True)
                self._atomic_write(page_path, stored_content)
            except OSError as exc:
                # The .srcs commit above already succeeded and is durable
                # (SimpleRCS.commit() writes+truncates the real file handle,
                # flushed by _close()) — .srcs and .md are now out of sync
                # for this page. There's no rollback here (see module
                # docstring); at minimum, make that loud rather than a bare
                # unmapped 500.
                logger.error(
                    "write_page: committed version %s to history for %r but failed to write "
                    "the live page file (%s) -- .srcs and .md are now out of sync for this page",
                    new_version,
                    rel_path,
                    exc,
                )
                raise InvalidPagePathError(rel_path, f"filesystem rejected this path: {exc}") from exc

        return PageContent(
            path=rel_path,
            content=stored_content,
            version=new_version,
            author=meta.get("author", author),
            date=meta.get("date", ""),
            message=meta.get("log", message),
        )

    def delete_page(self, path: str, *, author: str) -> None:
        rel_path = self._normalize_rel_path(path)
        page_path = self._page_fs_path(rel_path)
        history_path = self._history_fs_path(rel_path)

        with self._lock_for(rel_path):
            if not page_path.exists():
                raise PageNotFoundError(rel_path)

            if history_path.exists():
                rcs = SimpleRCS(str(history_path))
                try:
                    rcs.commit("", author=author, log="[deleted]")
                finally:
                    self._close(rcs)

            page_path.unlink()

    def get_history(self, path: str, limit: int | None = None) -> list[HistoryEntry]:
        rel_path = self._normalize_rel_path(path)
        history_path = self._history_fs_path(rel_path)

        with self._lock_for(rel_path):
            if not history_path.exists():
                raise PageNotFoundError(rel_path)

            rcs = SimpleRCS(str(history_path))
            try:
                entries = rcs.log(limit=limit)
            finally:
                self._close(rcs)

        return [
            HistoryEntry(
                version=e.get("ver", ""),
                author=e.get("author", ""),
                date=e.get("date", ""),
                message=e.get("log", ""),
            )
            for e in entries
        ]

    def get_version(self, path: str, version: str) -> PageContent:
        rel_path = self._normalize_rel_path(path)
        history_path = self._history_fs_path(rel_path)

        with self._lock_for(rel_path):
            if not history_path.exists():
                raise PageNotFoundError(rel_path)

            rcs = SimpleRCS(str(history_path))
            try:
                entries_by_version = {e["ver"]: e for e in rcs.log()}
                if version not in entries_by_version:
                    raise VersionNotFoundError(rel_path, version)
                content = rcs.checkout(version)
            finally:
                self._close(rcs)

        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        meta = entries_by_version[version]
        return PageContent(
            path=rel_path,
            content=content,
            version=version,
            author=meta.get("author", ""),
            date=meta.get("date", ""),
            message=meta.get("log", ""),
        )

    def diff_versions(self, path: str, version_a: str, version_b: str) -> str:
        rel_path = self._normalize_rel_path(path)
        history_path = self._history_fs_path(rel_path)

        with self._lock_for(rel_path):
            if not history_path.exists():
                raise PageNotFoundError(rel_path)

            rcs = SimpleRCS(str(history_path))
            try:
                known_versions = {e["ver"] for e in rcs.log()}
                for version in (version_a, version_b):
                    if version not in known_versions:
                        raise VersionNotFoundError(rel_path, version)
                return rcs.diff(version_a, version_b)
            finally:
                self._close(rcs)
