"""Read-only web catalog over authenticated fixed precomputed bundles."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from importlib import resources
import json
import logging
import mimetypes
from pathlib import Path, PurePosixPath
import threading
import time
from typing import Callable, Mapping

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    RequestValidationError,
    RunRequest,
    parse_run_request,
)
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.precomputed.compact_bundle import (
    CompactBundleError,
    CompactBundleValidation,
    CompactPlayback,
)
from fspm_optics.precomputed.committed_playback import (
    CommittedPlaybackCase,
    CommittedPlaybackPlan,
    build_committed_playback_plan,
    inspect_committed_playback_catalog as inspect_fixed_sweep,
    load_committed_case_bundle,
    validate_output_root,
)
from fspm_optics.precomputed.playback import (
    FixedPlaybackResolution,
    resolve_fixed_case,
    resolve_loaded_fixed_playback,
)
from fspm_optics.precomputed.target_adjustment import (
    DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S,
    validate_fspm_target_tolerance,
    validate_requested_target_ppfd,
)
from fspm_optics.web.precomputed_projection import (
    PUBLIC_CONVENTIONAL_GLB_BYTE_SIZE,
    PUBLIC_CONVENTIONAL_GLB_SHA256,
    PUBLIC_HPS_GLB_BYTE_SIZE,
    PUBLIC_HPS_GLB_SHA256,
    committed_viewer_paths,
    project_committed_authenticated_identities,
    project_committed_fixture_catalog,
    project_committed_json_artifact,
    project_committed_metrics,
    project_committed_result,
    project_committed_run_configuration,
    project_committed_viewer_scene,
    project_committed_visualization,
)


PLAYBACK_ID_LENGTH = 32
MAX_CACHED_PLAYBACKS = 8
DEFAULT_BASE_BUNDLE_CACHE_SIZE = 4
DEFAULT_MAX_SESSIONS = 256
DEFAULT_SESSION_TTL_SECONDS = 30 * 60.0
INITIAL_PLAYBACK_PIN_SECONDS = 10.0
_PROFILE_PREFIX = "profiles/rex_juvenile_preheading_12leaf_v1/"
_PROFILE_PAYLOADS = {
    "profile.v1.json": "plant_profile",
    "geometry.glb": "plant_geometry",
    "identity-map.v1.json": "plant_identity",
    "receivers.f32le.bin": "plant_receivers",
}
_SCATTER_VENDOR = {
    "vendor/three.module.js": "vendor/three.module.js",
    "vendor/three.core.js": "vendor/three.core.js",
    "vendor/addons/controls/OrbitControls.js": (
        "vendor/addons/controls/OrbitControls.js"
    ),
}
_SELECTOR_FIELDS = frozenset(
    {
        "system",
        "layout_mode",
        "room_length_ft",
        "room_width_ft",
        "aisle_mode",
        "target_ppfd",
        "lighting_target_mode",
        "fspm_target_tolerance",
    }
)

logger = logging.getLogger("uvicorn.error.fspm_optics.public.catalog")


class PrecomputedPlaybackError(ValueError):
    """A playback selector or authenticated bundle failed closed."""

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class PrecomputedPlaybackNotFound(LookupError):
    """An in-memory presentation ID is unknown or was evicted."""


@dataclass(frozen=True, slots=True)
class PlaybackArtifact:
    data: bytes
    media_type: str
    download_name: str | None = None
    etag: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "etag", f'"{_sha256(self.data)}"')


@dataclass(frozen=True, slots=True)
class WebPlayback:
    playback_id: str
    resolution: FixedPlaybackResolution
    _artifact_cache: dict[str, PlaybackArtifact] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _artifact_cache_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )
    _scatter_cache: tuple[Mapping[str, object], bytes] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def playback(self):  # type annotation follows the playback contract
        return self.resolution.playback

    def result_payload(self) -> dict[str, object]:
        result = project_committed_result(
            self.playback.result_payload(), self.resolution.case.system_id
        )
        prefix = f"/api/precomputed/playbacks/{self.playback_id}"
        viewer_prefix = f"/precomputed/{self.playback_id}"
        result.update(
            {
                "run_id": self.playback_id,
                "execution_mode": "precomputed",
                "metrics_url": f"{prefix}/metrics",
                "manifest_url": f"{prefix}/manifest",
                "ppfd_csv_url": f"{prefix}/artifacts/ppfd.csv",
                "plant_layout_viewer_url": f"{viewer_prefix}/viewer/index.html",
                "ppfd_heatmap_url": (
                    f"{prefix}/artifacts/ppfd-heatmap.png"
                ),
                "ppfd_heatmap_overlay_url": (
                    f"{prefix}/artifacts/ppfd-heatmap-overlay.png"
                ),
                "visualization_metadata_url": (
                    f"{prefix}/artifacts/visualization.json"
                ),
                "ppfd_scatter_viewer_url": (
                    f"{viewer_prefix}/scatter/index.html"
                ),
                "case_id": self.resolution.case.case_id,
                "bundle_identity_sha256": (
                    self.playback.canonical_bundle_identity_sha256
                ),
                "source_runtime_required": False,
            }
        )
        return result

    def metrics(self) -> dict[str, object]:
        metrics = project_committed_metrics(
            self.playback.metrics, self.resolution.case.system_id
        )
        visualization, _scatter = self._validated_ppfd_scatter()
        field = visualization.get("field")
        if isinstance(field, Mapping):
            identity = field.get("identity_sha256")
            sample_count = field.get("sample_count")
            record = metrics.get("visualization")
            if isinstance(record, dict):
                record["field_identity_sha256"] = identity
                record["sample_count"] = sample_count
        metrics["execution_mode"] = "precomputed"
        metrics["source_runtime_required"] = False
        return metrics

    def manifest(self) -> dict[str, object]:
        canonical = self.playback.canonical
        system_id = self.resolution.case.system_id
        run_configuration = canonical.public_payload["run_configuration"]
        authenticated_identities = canonical.public_payload[
            "authenticated_identities"
        ]
        if not isinstance(run_configuration, Mapping) or not isinstance(
            authenticated_identities, Mapping
        ):
            raise ValueError("authenticated presentation data is malformed.")
        run_configuration = project_committed_run_configuration(
            run_configuration, self.resolution.case
        )
        authenticated_identities = project_committed_authenticated_identities(
            authenticated_identities,
            self.resolution.case,
            self.playback.canonical_bundle_identity_sha256,
        )
        return {
            "schema_id": "fspm-optics.web-precomputed-playback",
            "schema_version": 2,
            "playback_id": self.playback_id,
            "system_id": system_id,
            "case_id": self.resolution.case.case_id,
            "bundle_identity_sha256": (
                self.playback.canonical_bundle_identity_sha256
            ),
            "derived_playback_identity_sha256": (
                self.playback.derived_playback_identity_sha256
            ),
            "fspm_target_tolerance": (
                self.playback.fspm_target_tolerance_umol_m2_s
            ),
            "fspm_tolerance_derivation_identity_sha256": (
                self.playback.fspm_tolerance_derivation_identity_sha256
            ),
            "presentation_identity_sha256": (
                self.playback.presentation_identity_sha256
            ),
            "requested_orientation": self.playback.presentation_metadata(),
            "run_configuration": run_configuration,
            "authenticated_identities": authenticated_identities,
            "capabilities": canonical.public_payload["capabilities"],
            "canonical_json_byte_identical": False,
            "source_runtime_required": False,
        }

    def artifact(self, name: str) -> PlaybackArtifact:
        cache_key = f"artifact:{name}"
        return self._cached_artifact(
            cache_key, lambda: self._build_artifact(name)
        )

    def _build_artifact(self, name: str) -> PlaybackArtifact:
        playback = self.playback
        canonical = playback.canonical
        if name == "ppfd.csv":
            filename, media_type, data = playback.final_baseline_ppfd_csv()
            return PlaybackArtifact(data, media_type, filename)
        if name == "visualization.json":
            metadata, _scatter = self._validated_ppfd_scatter()
            payload = deepcopy(dict(metadata))
            payload["run_id"] = self.playback_id
            return PlaybackArtifact(_json_bytes(payload), "application/json")
        if name == "ppfd-scatter.f32le.bin":
            _metadata, data = self._validated_ppfd_scatter()
            return PlaybackArtifact(data, "application/octet-stream")
        heatmaps = playback.heatmap_payloads()
        if name == "ppfd-heatmap.png":
            return PlaybackArtifact(bytes(heatmaps["plain_png"]), "image/png")
        if name == "ppfd-heatmap-overlay.png":
            return PlaybackArtifact(bytes(heatmaps["overlay_png"]), "image/png")
        payload_names = {
            "target-control.json": "target_control",
            "full-output-schedule.json": "full_output_schedule",
            "operating-point.json": "operating_point",
            "physical-source-state.json": "physical_source_state",
            "baseline-leaf-position-uniformity.v1.json": (
                "baseline_leaf_uniformity"
            ),
        }
        if name == "natural-fit-layout.json":
            data = _json_bytes(playback.natural_fit_layout())
            return PlaybackArtifact(
                project_committed_json_artifact(
                    name, data, self.resolution.case.system_id
                ),
                "application/json",
            )
        payload_name = payload_names.get(name)
        if payload_name is None:
            raise PrecomputedPlaybackNotFound(name)
        data = canonical.payloads[payload_name]
        data = project_committed_json_artifact(
            name, data, self.resolution.case.system_id
        )
        return PlaybackArtifact(data, "application/json")

    def viewer_artifact(self, relative: str) -> PlaybackArtifact:
        parts = _safe_parts(relative)
        path = PurePosixPath(*parts).as_posix()
        if _dynamic_viewer_path(path):
            return self._cached_artifact(
                f"viewer:{path}", lambda: self._build_viewer_artifact(path, parts)
            )
        return PlaybackArtifact(
            _resource_bytes("viewer", parts), _media_type(path)
        )

    def _build_viewer_artifact(
        self, path: str, parts: tuple[str, ...]
    ) -> PlaybackArtifact:
        canonical = self.playback.canonical
        if path == "scene.v1.json":
            return PlaybackArtifact(
                _json_bytes(self._viewer_scene()), "application/json"
            )
        if path == "instances.f32le.bin":
            return PlaybackArtifact(
                canonical.payloads["viewer_instances"],
                "application/octet-stream",
            )
        if path.startswith(_PROFILE_PREFIX):
            name = path.removeprefix(_PROFILE_PREFIX)
            payload_name = _PROFILE_PAYLOADS.get(name)
            if payload_name is not None:
                return PlaybackArtifact(
                    canonical.payloads[payload_name], _media_type(path)
                )
        if path == "fixtures/catalog.v1.json":
            return PlaybackArtifact(
                self._fixture_catalog_bytes(), "application/json"
            )
        fixture = self._fixture_payload(path)
        if fixture is not None:
            return fixture
        if path == "ppfd-heatmap/visualization.json":
            return PlaybackArtifact(
                self._viewer_visualization_bytes(), "application/json"
            )
        if path == "ppfd-heatmap/ppfd-scatter.f32le.bin":
            _metadata, scatter = canonical.validated_ppfd_scatter()
            return PlaybackArtifact(scatter, "application/octet-stream")
        if path.startswith(("fixtures/", "profiles/", "ppfd-heatmap/")):
            raise PrecomputedPlaybackNotFound(path)
        return PlaybackArtifact(
            _resource_bytes("viewer", parts), _media_type(path)
        )

    def scatter_artifact(self, relative: str) -> PlaybackArtifact:
        parts = _safe_parts(relative)
        path = PurePosixPath(*parts).as_posix()
        viewer_resource = _SCATTER_VENDOR.get(path)
        if viewer_resource is not None:
            return PlaybackArtifact(
                _resource_bytes("viewer", tuple(PurePosixPath(viewer_resource).parts)),
                _media_type(path),
            )
        return PlaybackArtifact(
            _resource_bytes("scatter", parts), _media_type(path)
        )

    def viewer_static_file(self, relative: str) -> tuple[Path, str] | None:
        parts = _safe_parts(relative)
        path = PurePosixPath(*parts).as_posix()
        if _dynamic_viewer_path(path):
            return None
        resource_path = _resource_path("viewer", parts)
        return None if resource_path is None else (resource_path, _media_type(path))

    def scatter_static_file(self, relative: str) -> tuple[Path, str] | None:
        parts = _safe_parts(relative)
        path = PurePosixPath(*parts).as_posix()
        viewer_resource = _SCATTER_VENDOR.get(path)
        if viewer_resource is not None:
            resource_path = _resource_path(
                "viewer", tuple(PurePosixPath(viewer_resource).parts)
            )
        else:
            resource_path = _resource_path("scatter", parts)
        return None if resource_path is None else (resource_path, _media_type(path))

    def _cached_artifact(
        self, key: str, factory: Callable[[], PlaybackArtifact]
    ) -> PlaybackArtifact:
        with self._artifact_cache_lock:
            cached = self._artifact_cache.get(key)
            if cached is not None:
                return cached
            built = factory()
            self._artifact_cache[key] = built
            return built

    def _validated_ppfd_scatter(
        self,
    ) -> tuple[Mapping[str, object], bytes]:
        with self._artifact_cache_lock:
            cached = self._scatter_cache
            if cached is None:
                cached = self.playback.validated_ppfd_scatter()
                cached = (
                    project_committed_visualization(
                        cached[0], self.resolution.case.system_id
                    ),
                    cached[1],
                )
                object.__setattr__(self, "_scatter_cache", cached)
            return cached

    def _viewer_scene(self) -> dict[str, object]:
        scene = deepcopy(dict(self.playback.viewer_scene()))
        return project_committed_viewer_scene(
            scene,
            self.resolution.case.system_id,
            fixture_catalog=self._fixture_catalog_bytes(),
            visualization=self._viewer_visualization_bytes(),
        )

    def _fixture_catalog_bytes(self) -> bytes:
        data = self.playback.canonical.payloads["fixture_catalog"]
        return project_committed_fixture_catalog(
            data, self.resolution.case.system_id
        )

    def _viewer_visualization_bytes(self) -> bytes:
        metadata, _scatter = self.playback.canonical.validated_ppfd_scatter()
        metadata = project_committed_visualization(
            metadata, self.resolution.case.system_id
        )
        return _json_bytes(metadata)

    def _fixture_payload(self, viewer_path: str) -> PlaybackArtifact | None:
        canonical = self.playback.canonical
        if self.resolution.case.system_id in {
            CONVENTIONAL_SYSTEM_ID,
            HPS_SYSTEM_ID,
        }:
            return self._projected_fixture_payload(viewer_path)
        records = canonical.manifest.get("payload_inventory")
        if isinstance(records, list):
            prefix = "payload/viewer/fixtures/"
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                payload_path = record.get("path")
                name = record.get("name")
                if (
                    isinstance(payload_path, str)
                    and isinstance(name, str)
                    and payload_path.startswith(prefix)
                    and payload_path.removeprefix(prefix) == viewer_path.removeprefix(
                        "fixtures/"
                    )
                    and name.startswith("fixture_transforms_")
                ):
                    return PlaybackArtifact(
                        canonical.payloads[name], "application/octet-stream"
                    )
        for raw in canonical.manifest.get("catalog_assets", []):
            if not isinstance(raw, Mapping):
                continue
            materialized = raw.get("materialized_path")
            expected_size = raw.get("byte_size")
            expected_sha = raw.get("sha256")
            if not isinstance(materialized, str):
                continue
            prefix = "plant-layout-viewer/"
            if not materialized.startswith(prefix) or materialized.removeprefix(
                prefix
            ) != viewer_path:
                continue
            data = _resource_bytes_by_identity(
                "viewer",
                ("fixtures",),
                expected_size=expected_size,
                expected_sha256=expected_sha,
            )
            return PlaybackArtifact(data, _media_type(viewer_path))
        return None

    def _projected_fixture_payload(
        self, viewer_path: str
    ) -> PlaybackArtifact | None:
        canonical = self.playback.canonical
        catalog_bytes = self._fixture_catalog_bytes()
        paths = committed_viewer_paths(catalog_bytes)
        if self.resolution.case.system_id == HPS_SYSTEM_ID:
            expected_size = PUBLIC_HPS_GLB_BYTE_SIZE
            expected_sha256 = PUBLIC_HPS_GLB_SHA256
            label = "HPS"
        else:
            expected_size = PUBLIC_CONVENTIONAL_GLB_BYTE_SIZE
            expected_sha256 = PUBLIC_CONVENTIONAL_GLB_SHA256
            label = "Conventional"
        if viewer_path == paths.asset:
            data = _resource_bytes_by_identity(
                "viewer",
                ("fixtures",),
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            return PlaybackArtifact(data, _media_type(viewer_path))
        if viewer_path != paths.transforms:
            return None
        candidates = [
            data
            for name, data in canonical.payloads.items()
            if name.startswith("fixture_transforms_")
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"authenticated {label} fixture transforms are malformed."
            )
        data = candidates[0]
        catalog = json.loads(catalog_bytes)
        matrices = catalog["asset_groups"][0]["instance_matrices"]
        if (
            len(data) != matrices["byte_length"]
            or _sha256(data) != matrices["sha256"]
        ):
            raise ValueError(
                f"authenticated {label} fixture transforms are invalid."
            )
        return PlaybackArtifact(data, "application/octet-stream")


@dataclass(slots=True)
class _LoadFlight:
    event: threading.Event = field(default_factory=threading.Event)
    value: object | None = None
    error: BaseException | None = None


@dataclass(slots=True)
class _PlaybackSession:
    selector: dict[str, object]
    cache_key: tuple[object, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _PreparedPlaybackRequest:
    request: RunRequest
    target: float | None
    lighting_target_mode: str | None
    fspm_target_tolerance: float
    case: CommittedPlaybackCase
    cache_key: tuple[object, ...]
    selector: dict[str, object]


class PrecomputedCatalog:
    """Thread-safe bounded catalog over immutable authenticated bundle data."""

    def __init__(
        self,
        root: str | Path,
        *,
        repository_root: str | Path,
        plan: CommittedPlaybackPlan | None = None,
        base_bundle_cache_size: int = DEFAULT_BASE_BUNDLE_CACHE_SIZE,
        playback_cache_size: int = MAX_CACHED_PLAYBACKS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = validate_output_root(
            root, repository_root=repository_root
        )
        self.plan = plan or build_committed_playback_plan()
        self.base_bundle_cache_size = _bounded_integer(
            base_bundle_cache_size, "base bundle cache size", maximum=24
        )
        self.playback_cache_size = _bounded_integer(
            playback_cache_size, "playback cache size", maximum=64
        )
        self.max_sessions = _bounded_integer(
            max_sessions, "maximum session count", maximum=4096
        )
        if self.max_sessions < self.playback_cache_size:
            raise ValueError(
                "maximum session count must be at least the playback cache size."
            )
        if (
            isinstance(session_ttl_seconds, bool)
            or not isinstance(session_ttl_seconds, int | float)
            or not 0.0 < float(session_ttl_seconds) <= 86_400.0
        ):
            raise ValueError("session TTL must be between 0 and 86400 seconds.")
        self.session_ttl_seconds = float(session_ttl_seconds)
        self._clock = clock
        self._base_cache: OrderedDict[str, CompactPlayback] = OrderedDict()
        self._playback_cache: OrderedDict[
            tuple[object, ...], WebPlayback
        ] = OrderedDict()
        self._sessions: OrderedDict[str, _PlaybackSession] = OrderedDict()
        self._base_flights: dict[str, _LoadFlight] = {}
        self._playback_flights: dict[tuple[object, ...], _LoadFlight] = {}
        self._reserved_playbacks: set[tuple[object, ...]] = set()
        self._initial_playback_pins: dict[tuple[object, ...], float] = {}
        self._initialization_flight: _LoadFlight | None = None
        self._availability: dict[str, object] | None = None
        self._ready = False
        self._lock = threading.RLock()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def initialize(self) -> None:
        """Authenticate the complete production catalog exactly once."""

        with self._lock:
            if self._ready:
                return
            flight = self._initialization_flight
            owner = flight is None
            if owner:
                flight = _LoadFlight()
                self._initialization_flight = flight
        assert flight is not None
        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return
        started = time.perf_counter()
        try:
            status = inspect_fixed_sweep(self.root, plan=self.plan)
            missing_count = sum(
                item.validation.status.value == "missing_bundle"
                for item in status.cases
            )
            if status.valid_count != self.plan.case_count:
                logger.error(
                    "catalog validation failed valid=%d missing=%d invalid=%d",
                    status.valid_count,
                    missing_count,
                    status.invalid_count,
                )
                raise RuntimeError(
                    "authenticated precomputed catalog is incomplete or invalid."
                )
            availability = self._availability_payload(status)
            with self._lock:
                self._availability = availability
                self._ready = True
                flight.value = True
            logger.info(
                "catalog ready plan=%s cases=%d elapsed_ms=%.3f",
                self.plan.plan_identity_sha256,
                self.plan.case_count,
                (time.perf_counter() - started) * 1000.0,
            )
        except BaseException as exc:
            with self._lock:
                flight.error = exc
            raise
        finally:
            with self._lock:
                self._initialization_flight = None
                flight.event.set()

    def availability(self) -> dict[str, object]:
        self.initialize()
        with self._lock:
            assert self._availability is not None
            return deepcopy(self._availability)

    def prepare_selector(self, selector: object) -> dict[str, object]:
        """Validate and normalize a selector without reading bundle data."""

        return dict(self._prepare(selector).selector)

    def load_if_cached(self, selector: object) -> WebPlayback | None:
        """Return an already materialized playback without starting work."""

        prepared = self._prepare(selector)
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            cached = self._playback_cache.get(prepared.cache_key)
            if cached is None:
                return None
            self._playback_cache.move_to_end(prepared.cache_key)
            self._register_session_locked(
                cached, prepared.cache_key, prepared.selector, now
            )
            return cached

    def load(self, selector: object, *, initial_pin: bool = True) -> WebPlayback:
        prepared = self._prepare(selector)
        request = prepared.request
        target = prepared.target
        lighting_target_mode = prepared.lighting_target_mode
        fspm_target_tolerance = prepared.fspm_target_tolerance
        case = prepared.case
        cache_key = prepared.cache_key
        normalized_selector = prepared.selector
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            cached = self._playback_cache.get(cache_key)
            if cached is not None:
                self._playback_cache.move_to_end(cache_key)
                self._register_session_locked(
                    cached, cache_key, normalized_selector, now
                )
                return cached
            flight = self._playback_flights.get(cache_key)
            owner = flight is None
            if owner:
                self._reserve_playback_locked(cache_key, now)
                flight = _LoadFlight()
                self._playback_flights[cache_key] = flight
        assert flight is not None
        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            assert isinstance(flight.value, WebPlayback)
            return flight.value
        try:
            base = self._load_base(case)
            if target is None:
                resolution = resolve_loaded_fixed_playback(
                    self.root,
                    request,
                    base,
                    plan=self.plan,
                    fspm_target_tolerance=fspm_target_tolerance,
                )
            else:
                resolution = resolve_loaded_fixed_playback(
                    self.root,
                    request,
                    base,
                    plan=self.plan,
                    target_ppfd_umol_m2_s=target,
                    lighting_target_mode=lighting_target_mode,
                    fspm_target_tolerance=fspm_target_tolerance,
                )
            record = WebPlayback(_playback_id(resolution), resolution)
            with self._lock:
                self._playback_cache[cache_key] = record
                self._playback_cache.move_to_end(cache_key)
                self._register_session_locked(
                    record, cache_key, normalized_selector, self._clock()
                )
                if initial_pin:
                    self._initial_playback_pins[cache_key] = (
                        self._clock() + INITIAL_PLAYBACK_PIN_SECONDS
                    )
                flight.value = record
            return record
        except CompactBundleError as exc:
            validation = exc.validation
            status_code = (
                404 if validation.status.value == "missing_bundle" else 409
            )
            error = PrecomputedPlaybackError(
                validation.status.value,
                "Authenticated precomputed data is unavailable.",
                status_code=status_code,
            )
            logger.error(
                "bundle load failed case=%s status=%s detail=%s",
                case.case_id,
                validation.status.value,
                validation.message,
            )
            with self._lock:
                flight.error = error
            raise error from exc
        except (RequestValidationError, ValueError) as exc:
            error = PrecomputedPlaybackError(
                "unavailable_precomputed_combination", str(exc), status_code=422
            )
            with self._lock:
                flight.error = error
            raise error from exc
        except BaseException as exc:
            with self._lock:
                flight.error = exc
            raise
        finally:
            with self._lock:
                self._playback_flights.pop(cache_key, None)
                self._reserved_playbacks.discard(cache_key)
                flight.event.set()

    def _prepare(self, selector: object) -> _PreparedPlaybackRequest:
        request, target, lighting_target_mode, fspm_target_tolerance = (
            self._request(selector)
        )
        try:
            case = resolve_fixed_case(
                request,
                plan=self.plan,
                **(
                    {}
                    if target is None
                    else {
                        "target_ppfd_umol_m2_s": target,
                        "lighting_target_mode": lighting_target_mode,
                    }
                ),
            )
        except (RequestValidationError, ValueError) as exc:
            raise PrecomputedPlaybackError(
                "unavailable_precomputed_combination", str(exc), status_code=422
            ) from exc
        cache_key: tuple[object, ...] = (
            case.case_id,
            float(request.room_length_ft),
            float(request.room_width_ft),
            target,
            lighting_target_mode,
            fspm_target_tolerance,
        )
        normalized_selector = _normalized_selector(
            request,
            target,
            lighting_target_mode,
            fspm_target_tolerance,
        )
        return _PreparedPlaybackRequest(
            request=request,
            target=target,
            lighting_target_mode=lighting_target_mode,
            fspm_target_tolerance=fspm_target_tolerance,
            case=case,
            cache_key=cache_key,
            selector=normalized_selector,
        )

    def get(self, playback_id: str) -> WebPlayback:
        if not _valid_playback_id(playback_id):
            raise PrecomputedPlaybackNotFound(playback_id)
        now = self._clock()
        with self._lock:
            self._cleanup_locked(now)
            session = self._sessions.get(playback_id)
            if session is None:
                raise PrecomputedPlaybackNotFound(playback_id)
            session.expires_at = now + self.session_ttl_seconds
            self._sessions.move_to_end(playback_id)
            record = self._playback_cache.get(session.cache_key)
            if record is not None:
                self._initial_playback_pins.pop(session.cache_key, None)
                self._playback_cache.move_to_end(session.cache_key)
                return record
            selector = dict(session.selector)
        record = self.load(selector, initial_pin=False)
        if record.playback_id != playback_id:
            raise PrecomputedPlaybackNotFound(playback_id)
        return record

    def cache_stats(self) -> dict[str, int]:
        with self._lock:
            self._cleanup_locked(self._clock())
            return {
                "base_bundles": len(self._base_cache),
                "playbacks": len(self._playback_cache),
                "sessions": len(self._sessions),
                "base_loads_in_flight": len(self._base_flights),
                "playback_loads_in_flight": len(self._playback_flights),
            }

    def shutdown(self) -> None:
        with self._lock:
            self._base_cache.clear()
            self._playback_cache.clear()
            self._sessions.clear()
            self._initial_playback_pins.clear()

    def _load_base(self, case: CommittedPlaybackCase) -> CompactPlayback:
        with self._lock:
            cached = self._base_cache.get(case.case_id)
            if cached is not None:
                self._base_cache.move_to_end(case.case_id)
                return cached
            flight = self._base_flights.get(case.case_id)
            owner = flight is None
            if owner:
                flight = _LoadFlight()
                self._base_flights[case.case_id] = flight
        assert flight is not None
        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            assert isinstance(flight.value, CompactPlayback)
            return flight.value
        try:
            base = load_committed_case_bundle(
                case.output_path(self.root),
                case,
                self.plan.plan_identity_sha256,
            )
            with self._lock:
                self._base_cache[case.case_id] = base
                self._base_cache.move_to_end(case.case_id)
                while len(self._base_cache) > self.base_bundle_cache_size:
                    self._base_cache.popitem(last=False)
                    logger.info("base bundle cache entry evicted")
                flight.value = base
            return base
        except BaseException as exc:
            with self._lock:
                flight.error = exc
            raise
        finally:
            with self._lock:
                self._base_flights.pop(case.case_id, None)
                flight.event.set()

    def _register_session_locked(
        self,
        record: WebPlayback,
        cache_key: tuple[object, ...],
        selector: dict[str, object],
        now: float,
    ) -> None:
        self._sessions[record.playback_id] = _PlaybackSession(
            selector=selector,
            cache_key=cache_key,
            expires_at=now + self.session_ttl_seconds,
        )
        self._sessions.move_to_end(record.playback_id)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
            logger.info("playback session evicted")

    def _cleanup_locked(self, now: float) -> None:
        expired = [
            playback_id
            for playback_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for playback_id in expired:
            self._sessions.pop(playback_id, None)
        if expired:
            logger.info("expired playback sessions removed count=%d", len(expired))
        expired_pins = [
            key
            for key, expires_at in self._initial_playback_pins.items()
            if expires_at <= now
        ]
        for key in expired_pins:
            self._initial_playback_pins.pop(key, None)
        active_keys = {session.cache_key for session in self._sessions.values()}
        stale_views = [
            key for key in self._playback_cache if key not in active_keys
        ]
        for key in stale_views:
            self._playback_cache.pop(key, None)

    def _reserve_playback_locked(
        self, cache_key: tuple[object, ...], now: float
    ) -> None:
        occupied = len(self._playback_cache) + len(self._reserved_playbacks)
        if occupied >= self.playback_cache_size:
            candidate = next(
                (
                    key
                    for key in self._playback_cache
                    if key not in self._initial_playback_pins
                ),
                None,
            )
            if candidate is None:
                logger.warning("playback capacity exhausted")
                raise PrecomputedPlaybackError(
                    "playback_capacity_exhausted",
                    "Playback capacity is busy; retry shortly.",
                    status_code=503,
                )
            self._playback_cache.pop(candidate, None)
            logger.info("inactive playback cache entry evicted")
        self._reserved_playbacks.add(cache_key)

    def _availability_payload(self, status) -> dict[str, object]:
        return {
            "schema_id": "fspm-optics.web-precomputed-availability",
            "schema_version": 1,
            "plan_identity_sha256": self.plan.plan_identity_sha256,
            "case_count": self.plan.case_count,
            "valid_case_count": status.valid_count,
            "missing_case_count": sum(
                item.validation.status.value == "missing_bundle"
                for item in status.cases
            ),
            "invalid_case_count": status.invalid_count,
            "cases": [
                {
                    "case_id": item.case.case_id,
                    "system": item.case.system_id,
                    "layout": item.case.layout_variant,
                    "aisle_mode": item.case.aisle_enabled,
                    "supported_room_orders_ft": item.case.canonical_domain[
                        "supported_requested_room_orders_ft"
                    ],
                    "canonical_display_room_ft": {
                        "length": item.case.canonical_domain[
                            "canonical_aligned_room"
                        ]["length_x_ft"],
                        "width": item.case.canonical_domain[
                            "canonical_aligned_room"
                        ]["width_y_ft"],
                    },
                    "available": item.complete,
                    "status": item.validation.status.value,
                    "message": (
                        "Bundle is authenticated and available."
                        if item.complete
                        else "Bundle is unavailable."
                    ),
                    "bundle_identity_sha256": (
                        item.validation.bundle_identity_sha256
                    ),
                    "controlled_settings": {
                        "quality": item.case.request.quality,
                        "mounting_height_in": item.case.request.mounting_height_in,
                        "analysis_scope": item.case.request.analysis_scope.value,
                        "include_far_red": item.case.request.include_far_red,
                        "fspm_target_mode": item.case.request.fspm_target_mode,
                        "fspm_target_tolerance_umol_m2_s": (
                            item.case.request.fspm_target_tolerance_umol_m2_s
                        ),
                        "solver": _public_solver(
                            item.case, item.validation
                        ),
                        "lighting_target_modes": (
                            []
                            if item.case.system_id == HPS_SYSTEM_ID
                            else ["mean_target", "target_capped"]
                        ),
                    },
                }
                for item in status.cases
            ],
        }

    def _request(
        self, selector: object
    ) -> tuple[RunRequest, float | None, str | None, float]:
        if not isinstance(selector, dict):
            raise PrecomputedPlaybackError(
                "invalid_precomputed_selector",
                "Precomputed playback selector must be a JSON object.",
                status_code=422,
            )
        unexpected = sorted(set(selector) - _SELECTOR_FIELDS)
        if unexpected:
            raise PrecomputedPlaybackError(
                "invalid_precomputed_selector",
                "Unsupported precomputed selector fields: " + ", ".join(unexpected),
                status_code=422,
            )
        system = selector.get("system")
        length = selector.get("room_length_ft")
        width = selector.get("room_width_ft")
        aisle = selector.get("aisle_mode", False)
        if not isinstance(aisle, bool):
            raise PrecomputedPlaybackError(
                "invalid_precomputed_selector",
                "aisle_mode must be a Boolean.",
                status_code=422,
            )
        try:
            fspm_target_tolerance = validate_fspm_target_tolerance(
                selector.get(
                    "fspm_target_tolerance",
                    DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S,
                )
            )
        except ValueError as exc:
            raise PrecomputedPlaybackError(
                "invalid_precomputed_fspm_target_tolerance",
                str(exc),
                status_code=422,
            ) from exc
        payload: dict[str, object] = {
            "system": system,
            "room_length_ft": length,
            "room_width_ft": width,
            "quality": "standard",
            "analysis_scope": "baseline_plus_multispectral_fspm",
            "include_far_red": True,
            "fspm_target_mode": "automatic",
            # The authenticated fixed case remains the historical 75.0
            # request. The public selector is applied only after validation.
            "fspm_target_tolerance": (
                DEFAULT_PRECOMPUTED_FSPM_TARGET_TOLERANCE_UMOL_M2_S
            ),
            "aisle_mode": aisle,
            "mounting_height_in": 24.0 if system == HPS_SYSTEM_ID else 18.0,
        }
        target: float | None = None
        lighting_target_mode: str | None = None
        if system == HPS_SYSTEM_ID:
            if (
                "target_ppfd" in selector
                or "lighting_target_mode" in selector
            ):
                raise PrecomputedPlaybackError(
                    "invalid_precomputed_selector",
                    "HPS precomputed playback does not accept a target "
                    "or target policy.",
                    status_code=422,
                )
        else:
            try:
                target = validate_requested_target_ppfd(
                    selector.get("target_ppfd", 250.0)
                )
            except ValueError as exc:
                raise PrecomputedPlaybackError(
                    "invalid_precomputed_target", str(exc), status_code=422
                ) from exc
            payload["target_ppfd"] = 250.0
            payload["lighting_target_mode"] = "mean_target"
            lighting_target_mode = selector.get(
                "lighting_target_mode", "mean_target"
            )
            if lighting_target_mode not in {"mean_target", "target_capped"}:
                raise PrecomputedPlaybackError(
                    "invalid_precomputed_selector",
                    "lighting_target_mode must be mean_target or target_capped.",
                    status_code=422,
                )
        if system == "conventional":
            layout = selector.get("layout_mode", "practical")
            if layout not in {"practical", "rolling_bench"}:
                raise PrecomputedPlaybackError(
                    "invalid_precomputed_selector",
                    "Conventional layout must be practical or rolling_bench.",
                    status_code=422,
                )
            payload["layout_mode"] = layout
        elif "layout_mode" in selector:
            raise PrecomputedPlaybackError(
                "invalid_precomputed_selector",
                "layout_mode is accepted only for Conventional playback.",
                status_code=422,
            )
        if system == "proposed":
            payload.update(
                {
                    "spectral_basis": "conventional_led_control",
                    "proposed_control_mode": "uniform_module_dimming",
                    "proposed_ring_mode": "reduced_one_ring",
                    "proposed_source_mode": "native_smd",
                }
            )
        try:
            request = parse_run_request(
                payload,
                proposed_layout_mode=ProposedLayoutMode.STANDALONE_MODULES,
            )
        except RequestValidationError as exc:
            raise PrecomputedPlaybackError(
                "invalid_precomputed_selector",
                exc.message,
                status_code=422,
            ) from exc
        return request, target, lighting_target_mode, fspm_target_tolerance


def _playback_id(resolution: FixedPlaybackResolution) -> str:
    playback = resolution.playback
    preimage = {
        "case_id": resolution.case.case_id,
        "bundle_identity_sha256": playback.canonical_bundle_identity_sha256,
        "derived_playback_identity_sha256": (
            playback.derived_playback_identity_sha256
        ),
        "presentation_identity_sha256": playback.presentation_identity_sha256,
    }
    return _sha256(_json_bytes(preimage))[:PLAYBACK_ID_LENGTH]


def _valid_playback_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == PLAYBACK_ID_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_selector(
    request: RunRequest,
    target: float | None,
    lighting_target_mode: str | None,
    fspm_target_tolerance: float,
) -> dict[str, object]:
    selector: dict[str, object] = {
        "system": request.system,
        "room_length_ft": float(request.room_length_ft),
        "room_width_ft": float(request.room_width_ft),
        "aisle_mode": bool(request.aisle_mode),
        "fspm_target_tolerance": fspm_target_tolerance,
    }
    if request.system == "conventional":
        selector["layout_mode"] = str(getattr(request, "layout_mode"))
    if target is not None:
        selector["target_ppfd"] = target
        selector["lighting_target_mode"] = lighting_target_mode or "mean_target"
    return selector


def _safe_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative:
        raise PrecomputedPlaybackNotFound(str(relative))
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PrecomputedPlaybackNotFound(relative)
    return tuple(path.parts)


def _resource_bytes(group: str, parts: tuple[str, ...]) -> bytes:
    node = resources.files("fspm_optics").joinpath("resources", group, *parts)
    if not node.is_file():
        raise PrecomputedPlaybackNotFound(PurePosixPath(*parts).as_posix())
    return node.read_bytes()


def _resource_path(group: str, parts: tuple[str, ...]) -> Path | None:
    node = resources.files("fspm_optics").joinpath("resources", group, *parts)
    if not node.is_file():
        raise PrecomputedPlaybackNotFound(PurePosixPath(*parts).as_posix())
    try:
        return Path(node).resolve(strict=True)
    except TypeError:
        return None


def _resource_bytes_by_identity(
    group: str,
    parts: tuple[str, ...],
    *,
    expected_size: object,
    expected_sha256: object,
) -> bytes:
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise PrecomputedPlaybackError(
            "catalog_asset_incompatible",
            "A committed viewer asset identity is malformed.",
            status_code=409,
        )
    root = resources.files("fspm_optics").joinpath("resources", group, *parts)
    pending = [root]
    while pending:
        node = pending.pop()
        if node.is_dir():
            pending.extend(node.iterdir())
            continue
        if not node.is_file():
            continue
        data = node.read_bytes()
        if len(data) == expected_size and _sha256(data) == expected_sha256:
            return data
    raise PrecomputedPlaybackError(
        "catalog_asset_incompatible",
        "A packaged viewer asset failed bundle authentication.",
        status_code=409,
    )


def _dynamic_viewer_path(path: str) -> bool:
    return path in {"scene.v1.json", "instances.f32le.bin"} or path.startswith(
        ("fixtures/", "profiles/", "ppfd-heatmap/")
    )


def _media_type(path: str) -> str:
    if path.endswith(".js"):
        return "text/javascript; charset=utf-8"
    if path.endswith(".json"):
        return "application/json"
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _public_solver(
    case: CommittedPlaybackCase,
    validation: CompactBundleValidation,
) -> dict[str, object]:
    manifest = validation.manifest
    if not validation.valid or not isinstance(manifest, Mapping):
        raise RuntimeError("available committed artifact lacks a valid manifest.")
    configuration = manifest.get("run_configuration")
    if not isinstance(configuration, Mapping):
        raise RuntimeError("committed artifact lacks its run configuration.")
    solver = configuration.get("solver")
    if not isinstance(solver, Mapping) or not solver:
        raise RuntimeError("committed artifact lacks its authenticated solver.")
    return {
        "backend": "authenticated_compact_playback",
        "source_runtime_required": False,
        "canonical_solver_identity_sha256": hashlib.sha256(
            json.dumps(
                solver,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }


def _bounded_integer(value: object, label: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{label} must be an integer between 1 and {maximum}.")
    return value


__all__ = [
    "PlaybackArtifact",
    "PrecomputedCatalog",
    "PrecomputedPlaybackError",
    "PrecomputedPlaybackNotFound",
    "WebPlayback",
]
