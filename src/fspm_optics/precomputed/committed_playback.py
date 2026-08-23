"""Closed compatibility contract for the 24 committed playback artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Final, Mapping

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    SUPPORTED_SYSTEM_IDS,
    RunRequest,
    parse_run_request,
)
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.precomputed.compact_bundle import (
    CompactBundleError,
    CompactBundleStatus,
    CompactBundleValidation,
    CompactPlayback,
    load_compact_bundle,
    validate_compact_bundle,
)
from fspm_optics.precomputed.contracts import (
    FIXED_CASE_BINDING_SCHEMA_ID,
    FIXED_CASE_BINDING_SCHEMA_VERSION,
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    PHYSICAL_ROOM_PAIRS_FT,
    canonical_room_domain,
)


COMMITTED_PLAYBACK_SCHEMA_ID: Final = (
    "fspm-optics.committed-playback-catalog"
)
COMMITTED_PLAYBACK_SCHEMA_VERSION: Final = 1
HISTORICAL_PLAN_IDENTITY_SHA256: Final = (
    "114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416"
)
HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256: Final = (
    "c2814d2836d11d800ec42cabbda16f856bce131afca61b449586f7d778d09fd1"
)
HISTORICAL_CASE_DIGEST_SEQUENCE_IDENTITY_SHA256: Final = (
    "4643582de4068b622ced2f576522558609ad2d0eca93660de0d37028b2e84f51"
)
HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256: Final = (
    "27bc3301c51cebc6c72cea8272a5d97b1c2a3e5b2ee6ef3fdd183eacba6dcdf9"
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIXED_OUTPUT_PUBLIC_SYSTEM_ID: Final = SUPPORTED_SYSTEM_IDS[2]


class CommittedPlaybackRole(StrEnum):
    PROPOSED = "proposed"
    CONTROLLED_REFERENCE = "controlled_reference"
    FIXED_OUTPUT_REFERENCE = "fixed_output_reference"


@dataclass(frozen=True, slots=True)
class _ArtifactGroup:
    role: CommittedPlaybackRole
    system_id: str
    layout_variant: str
    storage_directory: str
    case_slug: str


_GROUPS: Final = (
    _ArtifactGroup(
        CommittedPlaybackRole.PROPOSED,
        PROPOSED_SYSTEM_ID,
        "proposed_reduced_one_ring",
        PROPOSED_SYSTEM_ID,
        PROPOSED_SYSTEM_ID,
    ),
    _ArtifactGroup(
        CommittedPlaybackRole.CONTROLLED_REFERENCE,
        CONVENTIONAL_SYSTEM_ID,
        "practical",
        CONVENTIONAL_SYSTEM_ID,
        f"{CONVENTIONAL_SYSTEM_ID}-practical",
    ),
    _ArtifactGroup(
        CommittedPlaybackRole.CONTROLLED_REFERENCE,
        CONVENTIONAL_SYSTEM_ID,
        "rolling_bench",
        CONVENTIONAL_SYSTEM_ID,
        f"{CONVENTIONAL_SYSTEM_ID}-rolling-bench",
    ),
    _ArtifactGroup(
        CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE,
        _FIXED_OUTPUT_PUBLIC_SYSTEM_ID,
        "fixed_full_output",
        "hps",
        _FIXED_OUTPUT_PUBLIC_SYSTEM_ID,
    ),
)

# Closed artifact authority: historical case suffix plus exact compact-bundle
# identity. Source descriptions, fixture records, paths, and live resource
# identities deliberately do not enter this table.
_ARTIFACT_IDENTITIES: Final = (
    (
        "4d885c0fe70afcb6",
        "ce28c9a6806ff6923f2f76dabaaf6703fc2f41f37a58fd0bd606dd4f2fa1b198",
    ),
    (
        "de29baaa20bd3be7",
        "c30f0670617d34df556eb1ba6b25b8c7ba1d7dd7701a6e30b3d3083909a2d803",
    ),
    (
        "02cc1ffde5f6a41b",
        "af6a77675b67019f4c6b372ee211ff042fae44bba83f3a1e34ec1efd8502b338",
    ),
    (
        "72fbab3639afc123",
        "2e35e8a0011fc536c25c2178f5235f2cdcec596069c71e672f436301ed1bd130",
    ),
    (
        "2bc67e47980e2497",
        "187d4a8492e6f529d158c91edc7346ba6b04032faf4d995aa83c054edc07579a",
    ),
    (
        "1fcce32339e280db",
        "21c72aae4365b90b8ec16dc4f06b8bce86680151b43110d78407f9ffe5e150f3",
    ),
    (
        "5e38082b5b697173",
        "ad67a74006cbc7dd336c3f857929423023566e8e38a950f1bac58a74ed01968e",
    ),
    (
        "933aea01f5ae8bbe",
        "d2ba0bcee982b948ed2c2094e3e280ba0e2c074c3d5a32eb6fc8ae5d9287e1b3",
    ),
    (
        "d6ef9a1fba3c8d27",
        "0d8a04a25ff22da6f3c8c563aafe06598d4c658af9383f008fa7b63456be90db",
    ),
    (
        "c54ee9ed58943cf7",
        "c0937021cd7e59ee7fdc8df5f726e97c194fc8facb04164f06fcd9dd8483386b",
    ),
    (
        "a1b6a9eae5882ec3",
        "8df862109837eec5786b053af4545ba3969b1124815bb8d17ac2f89815b30ec4",
    ),
    (
        "b2ec67bf15a6f449",
        "276f051d0672f390dc91621ad23ef625d1f716ee79068008eea972aea463a781",
    ),
    (
        "4919a551599735bf",
        "5161ff479de325d1792223bb26ba1f7afdd40099433d6647e2c8e55aa453a096",
    ),
    (
        "e8be63b1cb0c4171",
        "164ef703395f8c44b539a18a570a76a74af42876c88e467ae799cd5e19824f49",
    ),
    (
        "f508d7b26129e200",
        "086a8b5eb0fbb2b91e1d41a15243496ab821fe1d2d0772d71d765737db8c7405",
    ),
    (
        "2d452ce182fb5e79",
        "3a736785f6cc577e619a9a9c73591a64269272e6b03925276610406d6578bd5f",
    ),
    (
        "e9776a389eafc647",
        "62dfe34e921eeb26fa671db28b1e0fdc87a6623ceb5b63d6e0288110db820e3e",
    ),
    (
        "7c59902a1a8b9ce8",
        "cb84eb4b021f1f63ad6644888304d1d6c24204a5f868739d2f39ec83234f6d9c",
    ),
    (
        "f6ca65661e3d53ed",
        "a2b182f14ae282af50191db7064830654c0dd2ec821cb6bacb559e0917d00d82",
    ),
    (
        "89dd72b4c790890b",
        "c169da60e925f54d1326a1c29cbaf74991ba09be95301f71be0995abf27cc824",
    ),
    (
        "9d00bca23343813b",
        "4818fcca11c2ada92408222f4587c90af5f45dc6e27ee033db4b157ea476288b",
    ),
    (
        "630a48333dec5ff5",
        "9f8c3133848665c2bcfb9621e704123261e495ce566c29c7b45bb8655fd23077",
    ),
    (
        "636194cd87973a8f",
        "da185173e555380220723f70ebcee4d4931277773bba2d96ce6363de9bd3dacf",
    ),
    (
        "a7008a97befee70d",
        "a6e9b1a086ab61c4ad04ee045f38e26677b5fce4db26a0c67d890991d89c3154",
    ),
)

# SHA-256 identities of the exact case IDs authenticated inside the immutable
# artifacts.  The compatibility contract deliberately stores only digests so
# the historical source vocabulary never becomes current application metadata.
_HISTORICAL_CASE_ID_SHA256: Final = (
    "580cbe36e1e471b54c8a82309590bbaa5f36effb6ed5bf6c97d808ed75d830ef",
    "6ce603806cbb86a917b4488f5df3646de6453cf262588912317719c6ee758815",
    "f25c822c9960347f1264fd75d5d748f796f27644d91fab37dff1d16973c7b9f3",
    "c4f6bd9ba1950cd9de9dc79d7da95d52813580211de8781b6c4855737811ce3a",
    "f6333e273d9d8b1986376607a4feb76896ff443ac83eacf280e7585fa2e14216",
    "bd7f7214cc4b28f699742ab8012de79f2b971d44f2c3298e359d170873ac1ffd",
    "a9ac8fdcc375319ef1d89c4329a2537dbea821b3d95f7439e3c55ec499466130",
    "dd17aa03c87adb14999f315650b7283bd18163646b76b7393519d6c107c8dc42",
    "dd55836517a6774560573adde828597a3b867bd7df9f14cb9891b88eab1b2643",
    "a64356f318292273c68a3f3e66157f38a370378ef4fd88aa59a90af36037eb3f",
    "55b4fa60025cbd51c981e33ea27a2cb74d92bb537f6b89c7b5bb9e8a71f7b861",
    "1a8efea25cd4d66a56f9428a57c6c08794572ed11c98b52d741d9fdde0ad0389",
    "b198866559412edec97c611b22747a6c0e2a03e70cd7c7dbfd751e29725cf8f7",
    "8ea55b6df98d0a1163dec46fc01f9ba4ec3ea2cac47907e44dd8f761d24b8dc1",
    "a1a94f2f1fd55d0f0715d9b2ace2183bf77e398760ccaa43bbb360509bc1983a",
    "da1395d084d520870d3bed9479e8c861d4eaa599ef46f555981f507e8c567085",
    "9a3375143c24193c080dc950dbaef6c19e01b1babc34060d1da29290d146cd93",
    "ca5097725a0eb819350ff11106c3cfbdff58dae75a3cb766ee2b819a6f1bfb3c",
    "7658bbc822ed6ef3247334d0b15cf5027dd0bc79c4bc480f0a6dd07351941ce3",
    "11d747ecf2d815efb2cb0d47e836dd487702ae84c0ed6cbcf538cb8cb706092f",
    "2fee3daac977c5015558fe10ebcdcf4605e9194db4f9bd4b75957d70f228e48c",
    "3dff5ba62f95cf1f618590da3ed1399d1b5e9ee83df3ac28e51215993754b9fc",
    "b3b6784e432653f730eee1315ed513f4cfd6d0d969edd997342584d472304797",
    "7edb8f10ed66f94cdeee8219f48033ae1e136e0c717b93ca05c49d26bff0a286",
)


@dataclass(frozen=True, slots=True)
class CommittedPlaybackCase:
    ordinal: int
    role: CommittedPlaybackRole
    case_id: str
    artifact_case_id_sha256: str
    bundle_identity_sha256: str
    system_id: str
    layout_variant: str
    aisle_enabled: bool
    canonical_domain: Mapping[str, object]
    request: RunRequest
    storage_relative_path: str

    def output_path(self, output_root: str | Path) -> Path:
        root = Path(output_root).expanduser().resolve()
        relative = PurePosixPath(self.storage_relative_path)
        return root.joinpath(*relative.parts)

    def bundle_validation_expectations(
        self, plan_identity_sha256: str
    ) -> dict[str, object]:
        return {
            "expected_bundle_identity_sha256": self.bundle_identity_sha256,
            "expected_run_configuration": {
                "fixed_plan": {
                    "schema_id": FIXED_CASE_BINDING_SCHEMA_ID,
                    "schema_version": FIXED_CASE_BINDING_SCHEMA_VERSION,
                    "plan_identity_sha256": plan_identity_sha256,
                }
            },
        }

    def authenticates_manifest(self, manifest: Mapping[str, object]) -> bool:
        configuration = manifest.get("run_configuration")
        if not isinstance(configuration, Mapping):
            return False
        fixed_plan = configuration.get("fixed_plan")
        if not isinstance(fixed_plan, Mapping):
            return False
        artifact_case_id = fixed_plan.get("case_id")
        return (
            isinstance(artifact_case_id, str)
            and hashlib.sha256(artifact_case_id.encode("utf-8")).hexdigest()
            == self.artifact_case_id_sha256
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "role": self.role.value,
            "case_id": self.case_id,
            "artifact_case_id_sha256": self.artifact_case_id_sha256,
            "bundle_identity_sha256": self.bundle_identity_sha256,
            "selector": {
                "system": self.system_id,
                "layout": self.layout_variant,
                "aisle_mode": self.aisle_enabled,
                "canonical_domain": dict(self.canonical_domain),
            },
            "storage_relative_path": self.storage_relative_path,
        }


@dataclass(frozen=True, slots=True)
class CommittedPlaybackPlan:
    cases: tuple[CommittedPlaybackCase, ...]
    plan_identity_sha256: str = HISTORICAL_PLAN_IDENTITY_SHA256

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_id": COMMITTED_PLAYBACK_SCHEMA_ID,
            "schema_version": COMMITTED_PLAYBACK_SCHEMA_VERSION,
            "plan_identity_sha256": self.plan_identity_sha256,
            "case_count": self.case_count,
            "historical_case_sequence_identity_sha256": (
                HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256
            ),
            "public_case_sequence_identity_sha256": _sequence_identity(
                [case.case_id for case in self.cases]
            ),
            "bundle_sequence_identity_sha256": (
                HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256
            ),
            "cases": [case.to_payload() for case in self.cases],
        }


@dataclass(frozen=True, slots=True)
class CommittedCaseStatus:
    case: CommittedPlaybackCase
    path: Path
    validation: CompactBundleValidation

    @property
    def complete(self) -> bool:
        return self.validation.status is CompactBundleStatus.VALID

    def to_payload(self) -> dict[str, object]:
        return {
            "ordinal": self.case.ordinal,
            "case_id": self.case.case_id,
            "path": str(self.path),
            "status": self.validation.status.value,
            "message": self.validation.message,
            "bundle_identity_sha256": self.validation.bundle_identity_sha256,
        }


@dataclass(frozen=True, slots=True)
class CommittedCatalogStatus:
    plan_identity_sha256: str
    output_root: Path
    cases: tuple[CommittedCaseStatus, ...]

    @property
    def valid_count(self) -> int:
        return sum(item.complete for item in self.cases)

    @property
    def remaining_count(self) -> int:
        return len(self.cases) - self.valid_count

    @property
    def invalid_count(self) -> int:
        return sum(
            item.validation.status
            not in {CompactBundleStatus.VALID, CompactBundleStatus.MISSING}
            for item in self.cases
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "plan_identity_sha256": self.plan_identity_sha256,
            "output_root": str(self.output_root),
            "total_cases": len(self.cases),
            "valid_or_skipped_cases": self.valid_count,
            "missing_or_pending_cases": sum(
                item.validation.status is CompactBundleStatus.MISSING
                for item in self.cases
            ),
            "invalid_or_incompatible_cases": self.invalid_count,
            "remaining_cases": self.remaining_count,
            "cases": [item.to_payload() for item in self.cases],
        }


def build_committed_playback_plan() -> CommittedPlaybackPlan:
    cases: list[CommittedPlaybackCase] = []
    for index, ((case_suffix, bundle_identity), artifact_case_id_sha256) in enumerate(
        zip(_ARTIFACT_IDENTITIES, _HISTORICAL_CASE_ID_SHA256, strict=True)
    ):
        group = _GROUPS[index // 6]
        group_index = index % 6
        physical_length_ft, physical_width_ft = PHYSICAL_ROOM_PAIRS_FT[
            group_index // 2
        ]
        long_ft = float(max(physical_length_ft, physical_width_ft))
        short_ft = float(min(physical_length_ft, physical_width_ft))
        aisle_enabled = bool(group_index % 2)
        room_slug = f"{int(long_ft)}x{int(short_ft)}"
        aisle_slug = "aisle-on" if aisle_enabled else "aisle-off"
        case_id = (
            f"{group.case_slug}-{room_slug}-ppfd-250-{aisle_slug}-"
            f"{case_suffix}"
        )
        storage_relative_path = (
            f"{group.storage_directory}/{group.layout_variant}/{room_slug}/"
            f"ppfd-250/{aisle_slug}.fspm-compact"
        )
        cases.append(
            CommittedPlaybackCase(
                ordinal=index + 1,
                role=group.role,
                case_id=case_id,
                artifact_case_id_sha256=artifact_case_id_sha256,
                bundle_identity_sha256=bundle_identity,
                system_id=group.system_id,
                layout_variant=group.layout_variant,
                aisle_enabled=aisle_enabled,
                canonical_domain=canonical_room_domain(long_ft, short_ft),
                request=_request_for_case(
                    group,
                    long_ft=long_ft,
                    short_ft=short_ft,
                    aisle_enabled=aisle_enabled,
                ),
                storage_relative_path=storage_relative_path,
            )
        )
    plan = CommittedPlaybackPlan(tuple(cases))
    _validate_committed_plan(plan)
    return plan


def validate_output_root(
    output_root: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    """Resolve one read-only catalog root without creating or changing it."""

    raw = Path(output_root).expanduser().absolute()
    for component in (raw, *raw.parents):
        if component.exists() and component.is_symlink():
            raise ValueError(
                "precomputed output path components must not be symlinks."
            )
    resolved = raw.resolve()
    forbidden = {Path(resolved.anchor), Path.home().resolve()}
    if repository_root is not None:
        forbidden.add(Path(repository_root).expanduser().resolve())
    if resolved in forbidden:
        raise ValueError("precomputed output root is too broad or protected.")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("precomputed output root must be a directory.")
    return resolved


def inspect_committed_playback_catalog(
    output_root: str | Path,
    *,
    plan: CommittedPlaybackPlan | None = None,
) -> CommittedCatalogStatus:
    """Authenticate every allowlisted artifact without live source authorities."""

    committed_plan = plan or build_committed_playback_plan()
    root = validate_output_root(output_root)
    statuses: list[CommittedCaseStatus] = []
    for case in committed_plan.cases:
        path = _validated_case_output_path(root, case)
        statuses.append(
            CommittedCaseStatus(
                case=case,
                path=path,
                validation=validate_committed_case_bundle(
                    path, case, committed_plan.plan_identity_sha256
                ),
            )
        )
    return CommittedCatalogStatus(
        committed_plan.plan_identity_sha256,
        root,
        tuple(statuses),
    )


def validate_committed_case_bundle(
    path: str | Path,
    case: CommittedPlaybackCase,
    plan_identity_sha256: str,
) -> CompactBundleValidation:
    validation = validate_compact_bundle(
        path,
        **case.bundle_validation_expectations(plan_identity_sha256),
    )
    if (
        validation.valid
        and isinstance(validation.manifest, Mapping)
        and not case.authenticates_manifest(validation.manifest)
    ):
        return CompactBundleValidation(
            CompactBundleStatus.IDENTITY_MISMATCH,
            "bundle case identity is not authorized for this artifact.",
            validation.bundle_identity_sha256,
            validation.manifest,
        )
    return validation


def load_committed_case_bundle(
    path: str | Path,
    case: CommittedPlaybackCase,
    plan_identity_sha256: str,
) -> CompactPlayback:
    playback = load_compact_bundle(
        path,
        **case.bundle_validation_expectations(plan_identity_sha256),
    )
    if not case.authenticates_manifest(playback.manifest):
        raise CompactBundleError(
            CompactBundleValidation(
                CompactBundleStatus.IDENTITY_MISMATCH,
                "bundle case identity is not authorized for this artifact.",
                str(playback.manifest.get("bundle_identity_sha256")),
                playback.manifest,
            )
        )
    return playback


def _validated_case_output_path(
    root: Path, case: CommittedPlaybackCase
) -> Path:
    path = case.output_path(root)
    for component in (path, *path.parents):
        if component == root.parent:
            break
        if component.exists() and component.is_symlink():
            raise ValueError("committed artifact path must not contain symlinks.")
    if not path.resolve().is_relative_to(root):
        raise ValueError("committed artifact path escapes the output root.")
    return path


def _request_for_case(
    group: _ArtifactGroup,
    *,
    long_ft: float,
    short_ft: float,
    aisle_enabled: bool,
) -> RunRequest:
    payload: dict[str, object] = {
        "system": group.system_id,
        "room_length_ft": long_ft,
        "room_width_ft": short_ft,
        "quality": "standard",
        "analysis_scope": "baseline_plus_multispectral_fspm",
        "include_far_red": True,
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 75.0,
        "aisle_mode": aisle_enabled,
        "mounting_height_in": (
            24.0
            if group.role is CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE
            else 18.0
        ),
    }
    if group.role is not CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE:
        payload["target_ppfd"] = FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S
        payload["lighting_target_mode"] = "mean_target"
    if group.role is CommittedPlaybackRole.CONTROLLED_REFERENCE:
        payload["layout_mode"] = group.layout_variant
    if group.role is CommittedPlaybackRole.PROPOSED:
        payload.update(
            {
                "spectral_basis": "conventional_led_control",
                "proposed_control_mode": "uniform_module_dimming",
                "proposed_ring_mode": "reduced_one_ring",
                "proposed_source_mode": "native_smd",
            }
        )
        return parse_run_request(
            payload,
            proposed_layout_mode=ProposedLayoutMode.STANDALONE_MODULES,
        )
    return parse_run_request(payload)


def _validate_committed_plan(plan: CommittedPlaybackPlan) -> None:
    if plan.plan_identity_sha256 != HISTORICAL_PLAN_IDENTITY_SHA256:
        raise ValueError("committed plan identity changed.")
    if plan.case_count != 24 or [case.ordinal for case in plan.cases] != list(
        range(1, 25)
    ):
        raise ValueError("committed playback catalog must contain exactly 24 cases.")
    case_ids = [case.case_id for case in plan.cases]
    artifact_case_ids = [case.artifact_case_id_sha256 for case in plan.cases]
    bundle_ids = [case.bundle_identity_sha256 for case in plan.cases]
    paths = [case.storage_relative_path for case in plan.cases]
    if len(set(case_ids)) != 24 or len(set(bundle_ids)) != 24 or len(set(paths)) != 24:
        raise ValueError("committed playback artifact identities must be unique.")
    if any(
        not _HEX_SHA256.fullmatch(identity)
        for identity in (*bundle_ids, *artifact_case_ids)
    ):
        raise ValueError("committed artifact identity is not a SHA-256 digest.")
    if (
        _sequence_identity(artifact_case_ids)
        != HISTORICAL_CASE_DIGEST_SEQUENCE_IDENTITY_SHA256
    ):
        raise ValueError("historical case digest sequence changed.")
    if (
        _sequence_identity(bundle_ids)
        != HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256
    ):
        raise ValueError("historical bundle sequence identity changed.")
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("committed playback storage path is unsafe.")


def _sequence_identity(values: list[str]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "COMMITTED_PLAYBACK_SCHEMA_ID",
    "COMMITTED_PLAYBACK_SCHEMA_VERSION",
    "HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256",
    "HISTORICAL_CASE_DIGEST_SEQUENCE_IDENTITY_SHA256",
    "HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256",
    "HISTORICAL_PLAN_IDENTITY_SHA256",
    "CommittedCatalogStatus",
    "CommittedCaseStatus",
    "CommittedPlaybackCase",
    "CommittedPlaybackPlan",
    "CommittedPlaybackRole",
    "build_committed_playback_plan",
    "inspect_committed_playback_catalog",
    "load_committed_case_bundle",
    "validate_committed_case_bundle",
    "validate_output_root",
]
