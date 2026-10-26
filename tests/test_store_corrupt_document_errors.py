"""Store PR 4: every per-module corrupt-document error is a StoreError, so one
that escapes a page shows the route-level storage banner. Synthetic values only."""

from __future__ import annotations

import pytest

from engine.account_attribution import CorruptAccountAttributionError
from engine.data_sources.candidate_store import CorruptCandidateStoreError
from engine.data_sources.choices import CorruptChoiceMapError
from engine.data_sources.committed import CorruptCommittedCacheError
from engine.instance_identity import CorruptInstanceOwnerError
from engine.portfolio_sync.portfolio import CorruptPortfolioCacheError
from engine.portfolio_sync.social_security import CorruptSSACacheError
from engine.store import CorruptDocumentError, CorruptStoreError, StoreError
from engine.ubs_activity_store import UbsActivityStoreError
from views.store_notice import store_error_message

_PATH_CAUSE_ERRORS = [
    CorruptAccountAttributionError,
    CorruptCandidateStoreError,
    CorruptChoiceMapError,
    CorruptCommittedCacheError,
    CorruptInstanceOwnerError,
    CorruptPortfolioCacheError,
    CorruptSSACacheError,
]


@pytest.mark.parametrize("cls", [*_PATH_CAUSE_ERRORS, UbsActivityStoreError])
def test_module_errors_are_store_errors_but_not_raw_row_errors(cls):
    assert issubclass(cls, CorruptDocumentError)
    assert issubclass(cls, StoreError)
    # engine code catches CorruptStoreError around raw Store.get calls; it must
    # not start swallowing these (Decision 8).
    assert not issubclass(cls, CorruptStoreError)


@pytest.mark.parametrize("cls", _PATH_CAUSE_ERRORS)
def test_the_banner_names_the_document_and_says_nothing_was_saved(cls):
    exc = cls("synthetic_ns", ValueError("synthetic truncation"))
    message = store_error_message(exc)
    assert message.startswith("Saved data could not be read: ")
    assert "synthetic_ns" in message
    assert message.endswith("Nothing was saved.")


def test_the_ubs_error_keeps_its_message():
    exc = UbsActivityStoreError("synthetic orders unreadable")
    assert str(exc) == "synthetic orders unreadable"
    assert "synthetic orders unreadable" in store_error_message(exc)
