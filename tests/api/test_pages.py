"""HTTP-level tests for the /pages API (GPC-188)."""


def test_create_page_returns_201_with_etag(client):
    resp = client.put("/pages/notes/hello.md", json={"content": "Hello\n", "message": "initial"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["path"] == "notes/hello.md"
    assert body["version"] == "1.0"
    assert body["content"] == "Hello\n"
    assert resp.headers["etag"] == '"1.0"'  # RFC 7232: entity-tag = quoted-string


def test_update_existing_page_returns_200_and_bumps_version(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.put("/pages/hello.md", json={"content": "v2\n"})
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.1"


def test_read_page(client):
    client.put("/pages/hello.md", json={"content": "hi\n", "message": "m"}, headers={"X-Wiki-Author": "alice"})
    resp = client.get("/pages/hello.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "hi\n"
    assert body["author"] == "alice"
    assert resp.headers["etag"] == '"1.0"'


def test_read_missing_page_is_404(client):
    assert client.get("/pages/missing.md").status_code == 404


def test_list_pages(client):
    client.put("/pages/a.md", json={"content": "a"})
    client.put("/pages/dir/b.md", json={"content": "b"})
    resp = client.get("/pages")
    assert resp.status_code == 200
    assert {p["path"] for p in resp.json()} == {"a.md", "dir/b.md"}


def test_history_is_newest_first(client):
    client.put("/pages/hello.md", json={"content": "v1\n", "message": "first"})
    client.put("/pages/hello.md", json={"content": "v2\n", "message": "second"})
    resp = client.get("/pages/hello.md/history")
    assert resp.status_code == 200
    assert [h["version"] for h in resp.json()] == ["1.1", "1.0"]


def test_get_specific_version(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    client.put("/pages/hello.md", json={"content": "v2\n"})
    resp = client.get("/pages/hello.md/versions/1.0")
    assert resp.status_code == 200
    assert resp.json()["content"] == "v1\n"


def test_get_missing_version_is_404(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    assert client.get("/pages/hello.md/versions/9.9").status_code == 404


def test_diff_between_versions(client):
    client.put("/pages/hello.md", json={"content": "line1\n"})
    client.put("/pages/hello.md", json={"content": "line1\nline2\n"})
    resp = client.get("/pages/hello.md/diff", params={"a": "1.0", "b": "1.1"})
    assert resp.status_code == 200
    assert "line2" in resp.json()["diff"]


def test_delete_then_read_is_404_but_history_survives(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.delete("/pages/hello.md", headers={"X-Wiki-Author": "alice"})
    assert resp.status_code == 204
    assert client.get("/pages/hello.md").status_code == 404

    history = client.get("/pages/hello.md/history").json()
    assert history[0]["message"] == "[deleted]"
    assert history[0]["author"] == "alice"


def test_delete_missing_page_is_404(client):
    assert client.delete("/pages/missing.md").status_code == 404


def test_if_match_rejects_stale_version(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.put("/pages/hello.md", json={"content": "v2\n"}, headers={"If-Match": "0.9"})
    assert resp.status_code == 409
    assert resp.json()["current_version"] == "1.0"


def test_if_match_accepts_current_version(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.put("/pages/hello.md", json={"content": "v2\n"}, headers={"If-Match": "1.0"})
    assert resp.status_code == 200


def test_if_none_match_star_rejects_existing_page(client):
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.put("/pages/hello.md", json={"content": "v2\n"}, headers={"If-None-Match": "*"})
    assert resp.status_code == 409


def test_if_none_match_star_allows_new_page(client):
    resp = client.put("/pages/hello.md", json={"content": "v1\n"}, headers={"If-None-Match": "*"})
    assert resp.status_code == 201


def test_path_traversal_never_succeeds(client):
    # httpx/Starlette normalize ".." out of URLs before routing in most
    # cases, so this mainly proves the request never reaches a 2xx either
    # way. See test_local_storage.py for a test that exercises the
    # validator itself with a raw ".." segment.
    resp = client.put("/pages/../../etc/passwd.md", json={"content": "pwned"})
    assert resp.status_code in (400, 404)


def test_path_must_end_with_md(client):
    assert client.put("/pages/notes/hello.txt", json={"content": "x"}).status_code == 400


def test_hidden_segment_rejected(client):
    assert client.put("/pages/.srcs/evil.md", json={"content": "x"}).status_code == 400


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- regression tests for the GPC-188 review findings ------------------------


def test_if_match_accepts_rfc_quoted_etag(client):
    """A spec-compliant client echoes the ETag header verbatim (quoted) as
    If-Match; that must work, not just the unquoted convention above."""
    client.put("/pages/hello.md", json={"content": "v1\n"})
    resp = client.put("/pages/hello.md", json={"content": "v2\n"}, headers={"If-Match": '"1.0"'})
    assert resp.status_code == 200


def test_recreate_page_after_delete_succeeds_with_if_none_match(client):
    """delete_page() is a soft delete (history keeps a tombstone commit), so
    recreating the page afterwards must be treated as a fresh create, not
    blocked as 'already exists' just because history is non-empty."""
    client.put("/pages/hello.md", json={"content": "v1\n"})
    client.delete("/pages/hello.md")
    assert client.get("/pages/hello.md").status_code == 404

    resp = client.put("/pages/hello.md", json={"content": "reborn\n"}, headers={"If-None-Match": "*"})
    assert resp.status_code == 201
    assert resp.json()["content"] == "reborn\n"


def test_page_path_containing_versions_segment_is_readable(client):
    """A page literally nested under a 'versions' directory must not be
    hijacked by the GET .../versions/{version} route."""
    resp = client.put("/pages/docs/versions/1.0.md", json={"content": "real page content\n"})
    assert resp.status_code == 201

    resp = client.get("/pages/docs/versions/1.0.md")
    assert resp.status_code == 200
    assert resp.json()["content"] == "real page content\n"


def test_page_path_containing_history_and_diff_segments_still_works(client):
    """Sanity check alongside the 'versions' case above: /history and /diff
    are safe by construction (a valid *.md page path can't end with either
    literal suffix), but worth pinning down explicitly."""
    for path in ("notes/history/log.md", "notes/diff/report.md"):
        assert client.put(f"/pages/{path}", json={"content": "x\n"}).status_code == 201
        assert client.get(f"/pages/{path}").status_code == 200


def test_page_path_collision_returns_400_not_500(client):
    """'foo.md' used first as a page, then as a directory prefix, must fail
    cleanly (bad input) rather than crash with an unhandled OSError."""
    assert client.put("/pages/foo.md", json={"content": "x"}).status_code == 201
    resp = client.put("/pages/foo.md/bar.md", json={"content": "y"})
    assert resp.status_code == 400


def test_control_character_in_path_rejected(client):
    resp = client.put("/pages/notes/evil%00.md", json={"content": "x"})
    assert resp.status_code == 400


def test_very_long_path_segment_rejected(client):
    resp = client.put(f"/pages/{'a' * 500}.md", json={"content": "x"})
    assert resp.status_code == 400
