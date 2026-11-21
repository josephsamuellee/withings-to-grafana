"""Profile mapping and discovery gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fitness_sync.config import load_config
from fitness_sync.profiles import (
    ProfileMappingError,
    discover_identities,
    require_mapped_person,
)
from fitness_sync.withings_api import TokenSet, WeightMeasurement, WithingsClient


def test_unmapped_person_rejected(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    # clear mapping
    from fitness_sync.config import PersonConfig

    bare = PersonConfig(person_id="person1", database="person1.sqlite3", identity_id=None)
    with pytest.raises(ProfileMappingError, match="identity_id"):
        require_mapped_person(bare)


def test_discovery_primary_only_blocks(config_path: Path) -> None:
    class Client(WithingsClient):
        def __init__(self) -> None:
            super().__init__(client_id="c", client_secret="s")

        def ensure_access_token(self, tokens, *, token_path=None):  # type: ignore[no-untyped-def]
            return tokens

        def iter_weight_measurements(self, access_token, **kwargs):  # type: ignore[no-untyped-def]
            return [
                WeightMeasurement(1, 100, 70.0, 111),
                WeightMeasurement(2, 200, 90.0, 111),  # same userid — not dual
            ]

    tokens = TokenSet("a", "r", 9999999999, userid=111)
    result = discover_identities(Client(), tokens, expected_persons=2)
    assert not result.dual_attributable
    assert 111 in result.identities_found
    assert "Blocking integration limitation" in result.detail


def test_discovery_dual_ok() -> None:
    class Client(WithingsClient):
        def __init__(self) -> None:
            super().__init__(client_id="c", client_secret="s")

        def ensure_access_token(self, tokens, *, token_path=None):  # type: ignore[no-untyped-def]
            return tokens

        def iter_weight_measurements(self, access_token, **kwargs):  # type: ignore[no-untyped-def]
            return [
                WeightMeasurement(1, 100, 70.0, 111),
                WeightMeasurement(2, 200, 90.0, 222),
            ]

    tokens = TokenSet("a", "r", 9999999999, userid=111)
    result = discover_identities(Client(), tokens)
    assert result.dual_attributable
    assert result.identities_found == [111, 222]
