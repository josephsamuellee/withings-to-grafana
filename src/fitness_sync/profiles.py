"""Explicit person ↔ Withings source identity mapping and discovery."""

from __future__ import annotations

from dataclasses import dataclass

from fitness_sync.config import AppConfig, PersonConfig
from fitness_sync.withings_api import TokenSet, WithingsClient


class ProfileMappingError(RuntimeError):
    pass


DUAL_PROFILE_BLOCKED_MESSAGE = (
    "Blocking integration limitation: the public Withings API did not expose "
    "reliably attributable measurements for both configured adults under the "
    "current authorization.\n\n"
    "Supported alternatives:\n"
    "  1. Independently authorized Withings accounts after user-approved "
    "migration of adult profiles (never automated by this tool).\n"
    "  2. A clearly labeled manual import path for the second person, if you "
    "export data yourself.\n\n"
    "Do not assign measurements by weight ranges or other heuristics. "
    "Profile migration/deletion is never performed automatically."
)


@dataclass(frozen=True)
class DiscoveryResult:
    identities_found: list[int]
    dual_attributable: bool
    detail: str


def require_mapped_person(person: PersonConfig) -> str:
    """Return identity_id or raise — never invent from measurements."""
    if not person.identity_id:
        raise ProfileMappingError(
            f"{person.person_id} has no identity_id mapping; "
            "run authorize/discovery and set an explicit Withings user id. "
            "Refusing to assign measurements by weight heuristics."
        )
    return person.identity_id


def token_path_for(config: AppConfig, person: PersonConfig):
    name = person.token_file or f"tokens_{person.person_id}.json"
    return config.paths.secrets_dir / name


def discover_identities(
    client: WithingsClient,
    tokens: TokenSet,
    *,
    expected_persons: int = 2,
) -> DiscoveryResult:
    """Read-only discovery using getmeas attribution signals.

    The public consumer API does not provide a general profile-selection
    parameter. We inspect userid on the authorized token and on returned
    measure groups. If only one identity is visible, dual sync is blocked.
    """
    tokens = client.ensure_access_token(tokens)
    measurements = client.iter_weight_measurements(tokens.access_token)
    userids: set[int] = set()
    if tokens.userid is not None:
        userids.add(int(tokens.userid))
    for m in measurements:
        if m.source_userid is not None:
            userids.add(int(m.source_userid))

    found = sorted(userids)
    dual = len(found) >= expected_persons
    if dual:
        detail = (
            f"Found {len(found)} attributable Withings user id(s): {found}. "
            "Dual-person sync may be configured with explicit mappings."
        )
    else:
        detail = (
            f"Found {len(found)} attributable Withings user id(s): {found or ['(none)']}. "
            + DUAL_PROFILE_BLOCKED_MESSAGE
        )
    return DiscoveryResult(
        identities_found=found,
        dual_attributable=dual,
        detail=detail,
    )


def assert_dual_sync_allowed(config: AppConfig, discovery: DiscoveryResult) -> None:
    enabled = [p for p in config.enabled_persons()]
    if len(enabled) < 2:
        return
    if not config.dual_person_sync_enabled or not discovery.dual_attributable:
        raise ProfileMappingError(discovery.detail)
    for p in enabled:
        require_mapped_person(p)
