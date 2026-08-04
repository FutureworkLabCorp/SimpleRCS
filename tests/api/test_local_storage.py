"""Direct (non-HTTP) tests for LocalFileStorage.

httpx/Starlette normalize ".." out of request URLs before routing in most
cases (see test_pages.py's traversal test), so the only way to actually
prove `_normalize_rel_path` itself rejects a raw ".." segment is to call
the storage layer directly, bypassing the HTTP client entirely.
"""

import threading
from pathlib import Path

import pytest

from wiki_api.storage.exceptions import (
    InvalidPagePathError,
    PageConflictError,
    PageNotFoundError,
    VersionNotFoundError,
)
from wiki_api.storage.local import LocalFileStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(root=tmp_path / "wiki")


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "notes/../../../etc/passwd.md",
        "../escape.md",
        "notes/.hidden.md",
        ".srcs/collide.md",
        "notes/readme.txt",
        "notes/evil\x00.md",  # embedded NUL: pathlib.resolve() raises a bare ValueError otherwise
        "notes/weird\ntitle.md",  # embedded LF: SimpleRCS treats any '\n'-containing string as
        # raw content rather than a path (see SimpleRCS.__init__), silently skipping history creation
        "a" * 500 + ".md",  # single segment past the 200-byte headroom under typical NAME_MAX
    ],
)
def test_rejects_invalid_paths(storage: LocalFileStorage, bad_path: str) -> None:
    with pytest.raises(InvalidPagePathError):
        storage.page_exists(bad_path)


def test_write_creates_md_file_and_srcs_history_side_by_side(storage: LocalFileStorage, tmp_path: Path) -> None:
    storage.write_page("notes/hello.md", "hi\n", author="alice", message="first")

    assert (tmp_path / "wiki" / "notes" / "hello.md").read_text() == "hi\n"
    assert (tmp_path / "wiki" / ".srcs" / "notes" / "hello.md.srcs").exists()


def test_read_after_write_round_trips(storage: LocalFileStorage) -> None:
    written = storage.write_page("hello.md", "content\n", author="alice")
    read = storage.read_page("hello.md")
    assert read.content == written.content == "content\n"
    assert read.version == "1.0"


def test_read_missing_page_raises(storage: LocalFileStorage) -> None:
    with pytest.raises(PageNotFoundError):
        storage.read_page("missing.md")


def test_delete_leaves_tombstone_in_history(storage: LocalFileStorage) -> None:
    storage.write_page("hello.md", "v1\n", author="alice")
    storage.delete_page("hello.md", author="bob")

    with pytest.raises(PageNotFoundError):
        storage.read_page("hello.md")

    history = storage.get_history("hello.md")
    assert history[0].message == "[deleted]"
    assert history[0].author == "bob"


def test_if_match_conflict_reports_current_version(storage: LocalFileStorage) -> None:
    storage.write_page("hello.md", "v1\n", author="alice")
    with pytest.raises(PageConflictError) as exc_info:
        storage.write_page("hello.md", "v2\n", author="alice", if_match="0.9")
    assert exc_info.value.current_version == "1.0"


def test_get_version_unknown_raises(storage: LocalFileStorage) -> None:
    storage.write_page("hello.md", "v1\n", author="alice")
    with pytest.raises(VersionNotFoundError):
        storage.get_version("hello.md", "9.9")


def test_list_pages_excludes_history_dir(storage: LocalFileStorage) -> None:
    storage.write_page("a.md", "a", author="alice")
    storage.write_page("dir/b.md", "b", author="alice")

    paths = {p.path for p in storage.list_pages()}
    assert paths == {"a.md", "dir/b.md"}


# -- regression tests for the GPC-188 review findings ------------------------


def test_if_none_match_star_allows_recreate_after_soft_delete(storage: LocalFileStorage) -> None:
    """The create-only precondition must key off the live page, not history
    presence -- delete_page()'s tombstone commit must not make a since-
    deleted page look 'still existing' to a later If-None-Match: * write."""
    storage.write_page("hello.md", "v1\n", author="alice")
    storage.delete_page("hello.md", author="alice")

    written = storage.write_page("hello.md", "reborn\n", author="bob", if_none_match="*")
    assert written.content == "reborn\n"


def test_if_match_accepts_quoted_etag(storage: LocalFileStorage) -> None:
    storage.write_page("hello.md", "v1\n", author="alice")
    written = storage.write_page("hello.md", "v2\n", author="alice", if_match='"1.0"')
    assert written.version == "1.1"


def test_page_path_directory_collision_raises_invalid_path_not_oserror(storage: LocalFileStorage) -> None:
    """'foo.md' as a file, then as a directory prefix for another page, must
    surface as InvalidPagePathError (400 at the API layer), not an unhandled
    OSError from Path.mkdir()."""
    storage.write_page("foo.md", "x", author="alice")
    with pytest.raises(InvalidPagePathError):
        storage.write_page("foo.md/bar.md", "y", author="alice")


def test_concurrent_read_is_serialized_against_in_flight_write(
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the per-page lock now covers reads, not just writes: a reader
    that arrives while a write is in its critical section must block until
    the write finishes, rather than observing a torn intermediate state.

    Regression test for the confirmed GPC-188 review finding that read_page
    et al. never took the lock write_page/delete_page use.
    """
    storage.write_page("hello.md", "v1\n", author="alice")

    write_reached_critical_section = threading.Event()
    release_write = threading.Event()
    original_atomic_write = LocalFileStorage._atomic_write  # unbound: staticmethod already unwraps to a plain function

    def slow_atomic_write(path, content):
        write_reached_critical_section.set()
        assert release_write.wait(timeout=5), "test deadlocked waiting to release the writer"
        original_atomic_write(path, content)

    monkeypatch.setattr(LocalFileStorage, "_atomic_write", staticmethod(slow_atomic_write))

    read_results = []

    def do_write() -> None:
        storage.write_page("hello.md", "v2\n", author="bob")

    def do_read() -> None:
        assert write_reached_critical_section.wait(timeout=5), "writer never reached its critical section"
        read_results.append(storage.read_page("hello.md"))

    writer = threading.Thread(target=do_write)
    reader = threading.Thread(target=do_read)
    writer.start()
    reader.start()

    assert write_reached_critical_section.wait(timeout=5)
    # The writer is now paused mid-critical-section, holding the per-page
    # lock. If read_page() also takes that lock (the fix), the reader thread
    # must still be blocked here, however long we give it.
    reader.join(timeout=0.3)
    assert read_results == [], "reader ran while the writer held the lock -- lock no longer covers reads"

    release_write.set()
    writer.join(timeout=5)
    reader.join(timeout=5)

    assert read_results[0].content == "v2\n"
    assert read_results[0].version == "1.1"
