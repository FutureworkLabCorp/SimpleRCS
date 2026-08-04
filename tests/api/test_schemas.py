"""Direct tests for Pydantic-level validation (GPC-188 review findings)."""

import pytest
from pydantic import ValidationError

from wiki_api.schemas import PageWriteRequest


def test_content_within_limit_is_accepted() -> None:
    PageWriteRequest(content="x" * 100)


def test_content_over_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PageWriteRequest(content="x" * 20_000_001)
