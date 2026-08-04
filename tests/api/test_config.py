"""Direct tests for Settings validation (GPC-188 review findings)."""

import pytest
from pydantic import ValidationError

from wiki_api.config import Settings


def test_default_history_dirname_is_accepted() -> None:
    Settings()


@pytest.mark.parametrize("bad_dirname", ["shared", "", ".", "..", ".srcs/nested"])
def test_non_hidden_history_dirname_is_rejected(bad_dirname: str) -> None:
    """A non-dot-prefixed (or multi-segment) history_dirname would silently
    break _normalize_rel_path's 'segments starting with . are reserved for
    history_dirname' assumption -- and list_pages()'s exclusion filter --
    letting ordinary pages alias into the 'reserved' directory and vanish
    from GET /pages. Reject it at config-load time instead."""
    with pytest.raises(ValidationError):
        Settings(history_dirname=bad_dirname)
