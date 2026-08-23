"""Validated metadata for a scalar isolated-rtrace SMD basis matrix."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Final, Mapping

from fspm_optics.layout.fixture_plan import (
    STANDALONE_FIXTURE_POLICY_ID,
    fixture_policy_id,
)
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from fspm_optics.layout.ring import (
    DEFAULT_PROPOSED_RING_MODE,
    ProposedRingMode,
    proposed_module_pattern_id,
    resolve_proposed_ring_mode,
)
from fspm_optics.fixtures.smd.config import mechanical_envelope
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    production_room_model_payload,
)
from fspm_optics.transport.scalar_ppfd import (
    BASELINE_PPFD_RGB_DECODE_METHOD,
    BASELINE_PPFD_TRANSPORT_BASIS,
)

ISOLATED_RTRACE_BACKEND: Final = "isolated_rtrace"


@dataclass(frozen=True, slots=True)
class BasisManifest:
    """Shape and transport contract for one SMD control-zone basis."""

    room_length_m: float
    room_width_m: float
    room_height_m: float
    sensor_count: int
    control_zone_count: int
    matrix_shape: tuple[int, int]
    radiance_options: tuple[str, ...]
    nthreads: int
    reference_watts: float
    layout_module_count: int
    module_profile_id: str
    ambient_cache_policy: str
    proposed_layout_mode: ProposedLayoutMode = DEFAULT_PROPOSED_LAYOUT_MODE
    proposed_ring_mode: ProposedRingMode = DEFAULT_PROPOSED_RING_MODE
    module_pattern_id: str = "centered_square_full_v1"
    fixture_policy_id: str = STANDALONE_FIXTURE_POLICY_ID
    mechanical_envelope_id: str = "standalone_module_150x150mm_v1"
    module_footprint_x_m: float = 0.15
    module_footprint_y_m: float = 0.15
    fixture_asset_set_id: str = "proposed-led-module-v1"
    room_model_identity_sha256: str = PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
    backend: str = ISOLATED_RTRACE_BACKEND
    scalar_ppfd_decode_policy: str = BASELINE_PPFD_RGB_DECODE_METHOD
    scalar_ppfd_transport_basis: str = BASELINE_PPFD_TRANSPORT_BASIS
    emitter_source_sha256: str | None = None
    emitter_metadata_sha256: str | None = None
    room_text_sha256: str | None = None
    sensor_text_sha256: str | None = None
    emitter_text_sha256_by_control_zone: tuple[str, ...] = ()
    command_policy_sha256: str | None = None
    fixture_occlusion_identity: str | None = None
    fixture_occlusion_manifest_sha256: str | None = None
    proposed_source_mode: str = "native_smd"
    proposed_source_identity_sha256: str | None = None
    angular_data_sha256: str | None = None
    source_classification: str | None = None
    emitter_shape: str | None = None
    emitter_area_per_module_m2: float | None = None
    emitter_diameter_m: float | None = None
    authenticated_ies_sha256: str | None = None
    normalized_angular_identity_sha256: str | None = None
    angular_normalization_policy: str | None = None
    completed_aperture_characterization_identity_sha256: str | None = None
    controlled_spd_identity_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("room_length_m", "room_width_m", "room_height_m"):
            object.__setattr__(self, name, _positive_number(name, getattr(self, name)))
        for name in (
            "sensor_count",
            "control_zone_count",
            "nthreads",
            "layout_module_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        reference = _positive_number("reference_watts", self.reference_watts)
        object.__setattr__(self, "reference_watts", reference)
        shape = tuple(self.matrix_shape)
        if len(shape) != 2 or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in shape
        ):
            raise ValueError("matrix_shape must contain two positive integers.")
        expected_shape = (self.sensor_count, self.control_zone_count)
        if shape != expected_shape:
            raise ValueError(
                "basis matrix shape must equal sensors x control_zones: "
                f"expected {expected_shape}, got {shape}."
            )
        object.__setattr__(self, "matrix_shape", shape)
        options = tuple(str(option) for option in self.radiance_options)
        if any(not option for option in options):
            raise ValueError("radiance_options must contain non-empty tokens.")
        object.__setattr__(self, "radiance_options", options)
        if self.backend != ISOLATED_RTRACE_BACKEND:
            raise ValueError(
                f"basis backend is fixed to {ISOLATED_RTRACE_BACKEND!r}."
            )
        if self.room_model_identity_sha256 != PRODUCTION_ROOM_MODEL_IDENTITY_SHA256:
            raise ValueError(
                "basis manifest room model is not the current production authority."
            )
        for name in (
            "module_profile_id",
            "ambient_cache_policy",
            "scalar_ppfd_decode_policy",
            "scalar_ppfd_transport_basis",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string.")
        mode = resolve_proposed_layout_mode(self.proposed_layout_mode)
        object.__setattr__(self, "proposed_layout_mode", mode)
        ring_mode = resolve_proposed_ring_mode(self.proposed_ring_mode)
        object.__setattr__(self, "proposed_ring_mode", ring_mode)
        if self.module_pattern_id != proposed_module_pattern_id(ring_mode):
            raise ValueError(
                "module_pattern_id does not match proposed_ring_mode."
            )
        if (
            ring_mode is ProposedRingMode.REDUCED_ONE_RING
            and mode is not ProposedLayoutMode.STANDALONE_MODULES
        ):
            raise ValueError(
                "reduced_one_ring basis manifests require standalone_modules."
            )
        if self.fixture_policy_id != fixture_policy_id(mode):
            raise ValueError(
                "fixture_policy_id does not match proposed_layout_mode."
            )
        envelope_id, footprint_x, footprint_y = mechanical_envelope(mode)
        expected_asset_set = (
            "proposed-led-module-v1"
            if mode is ProposedLayoutMode.STANDALONE_MODULES
            else "historical-proposed-fixture-assets-v1"
        )
        if (
            self.mechanical_envelope_id != envelope_id
            or not math.isclose(
                self.module_footprint_x_m,
                footprint_x,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            or not math.isclose(
                self.module_footprint_y_m,
                footprint_y,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            or self.fixture_asset_set_id != expected_asset_set
        ):
            raise ValueError(
                "mechanical envelope or fixture asset set does not match "
                "proposed_layout_mode."
            )
        for name in (
            "emitter_source_sha256",
            "emitter_metadata_sha256",
            "room_text_sha256",
            "sensor_text_sha256",
            "command_policy_sha256",
            "fixture_occlusion_identity",
            "fixture_occlusion_manifest_sha256",
            "proposed_source_identity_sha256",
            "angular_data_sha256",
            "source_classification",
            "emitter_shape",
            "authenticated_ies_sha256",
            "normalized_angular_identity_sha256",
            "angular_normalization_policy",
            "completed_aperture_characterization_identity_sha256",
            "controlled_spd_identity_sha256",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None.")
        emitter_hashes = tuple(self.emitter_text_sha256_by_control_zone)
        if emitter_hashes and len(emitter_hashes) != self.control_zone_count:
            raise ValueError(
                "emitter_text_sha256_by_control_zone must contain one hash per "
                "control zone."
            )
        if any(not isinstance(value, str) or not value for value in emitter_hashes):
            raise ValueError("emitter text hashes must be non-empty strings.")
        object.__setattr__(
            self, "emitter_text_sha256_by_control_zone", emitter_hashes
        )
        if self.proposed_source_mode not in (
            "native_smd",
            "cob_source_shape_surrogate",
        ):
            raise ValueError("unsupported Proposed source mode in basis manifest.")
        for name in ("emitter_area_per_module_m2", "emitter_diameter_m"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _positive_number(name, value))

    @property
    def n_vars(self) -> int:
        """Legacy comparison alias for control-zone count."""

        return self.control_zone_count

    @property
    def n_rings(self) -> int:
        """Legacy comparison alias; old ring indices map to control zones."""

        return self.control_zone_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 5,
            "backend": self.backend,
            "room_dimensions_m": {
                "length": self.room_length_m,
                "width": self.room_width_m,
                "height": self.room_height_m,
            },
            "sensor_count": self.sensor_count,
            "control_zone_count": self.control_zone_count,
            "matrix_shape": list(self.matrix_shape),
            "n_vars": self.n_vars,
            "n_rings": self.n_rings,
            "reference_watts": self.reference_watts,
            "layout_module_count": self.layout_module_count,
            "module_profile_id": self.module_profile_id,
            "layout_composition": {
                "proposed_layout_mode": self.proposed_layout_mode.value,
                "proposed_ring_mode": self.proposed_ring_mode.value,
                "module_pattern_id": self.module_pattern_id,
                "fixture_policy_id": self.fixture_policy_id,
                "mechanical_envelope": {
                    "id": self.mechanical_envelope_id,
                    "width_x_m": self.module_footprint_x_m,
                    "height_y_m": self.module_footprint_y_m,
                },
                "fixture_asset_set_id": self.fixture_asset_set_id,
            },
            "scalar_ppfd": {
                "decode_policy": self.scalar_ppfd_decode_policy,
                "transport_basis": self.scalar_ppfd_transport_basis,
            },
            "radiance": {
                "options": list(self.radiance_options),
                "nthreads": self.nthreads,
                "ambient_cache_policy": self.ambient_cache_policy,
            },
            "room_model": production_room_model_payload()
            | {"identity_sha256": self.room_model_identity_sha256},
            "emitter_hashes": {
                "source_sha256": self.emitter_source_sha256,
                "metadata_sha256": self.emitter_metadata_sha256,
                "by_control_zone": list(
                    self.emitter_text_sha256_by_control_zone
                ),
            },
            "workspace_hashes": {
                "room_text_sha256": self.room_text_sha256,
                "sensor_text_sha256": self.sensor_text_sha256,
                "command_policy_sha256": self.command_policy_sha256,
            },
            "fixture_occlusion": {
                "identity_sha256": self.fixture_occlusion_identity,
                "classification_manifest_sha256": (
                    self.fixture_occlusion_manifest_sha256
                ),
                "included_in_every_basis_octree": True,
            },
            "proposed_source": {
                "source_mode": self.proposed_source_mode,
                "identity_sha256": self.proposed_source_identity_sha256,
                "classification": self.source_classification,
                "emitter_geometry": {
                    "shape": self.emitter_shape,
                    "area_per_module_m2": self.emitter_area_per_module_m2,
                    "diameter_m": self.emitter_diameter_m,
                },
                "authenticated_ies_sha256": self.authenticated_ies_sha256,
                "normalized_angular_identity_sha256": (
                    self.normalized_angular_identity_sha256
                ),
                "angular_data_sha256": self.angular_data_sha256,
                "angular_normalization_policy": (
                    self.angular_normalization_policy
                ),
                "completed_aperture_characterization_identity_sha256": (
                    self.completed_aperture_characterization_identity_sha256
                ),
                "controlled_spd_identity_sha256": (
                    self.controlled_spd_identity_sha256
                ),
                "complete_source_in_every_basis_column": True,
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BasisManifest":
        schema_version = payload.get("schema_version", 1)
        if schema_version != 5:
            raise ValueError(
                "unsupported basis manifest schema_version; pre-room-authority "
                "artifacts cannot be resumed."
            )
        room = _mapping(payload.get("room_dimensions_m"), "room_dimensions_m")
        scalar = _mapping(payload.get("scalar_ppfd"), "scalar_ppfd")
        radiance = _mapping(payload.get("radiance"), "radiance")
        hashes = _mapping(payload.get("emitter_hashes", {}), "emitter_hashes")
        workspace_hashes = _mapping(
            payload.get("workspace_hashes", {}), "workspace_hashes"
        )
        composition = _mapping(
            payload.get("layout_composition"),
            "layout_composition",
        )
        proposed_layout_mode = resolve_proposed_layout_mode(
            composition.get("proposed_layout_mode")
        )
        resolved_fixture_policy_id = str(composition.get("fixture_policy_id"))
        proposed_ring_mode = resolve_proposed_ring_mode(
            composition.get("proposed_ring_mode")
        )
        module_pattern_id = str(
            composition.get(
                "module_pattern_id",
                proposed_module_pattern_id(proposed_ring_mode),
            )
        )
        room_model = _mapping(payload.get("room_model"), "room_model")
        expected_room_model = production_room_model_payload() | {
            "identity_sha256": PRODUCTION_ROOM_MODEL_IDENTITY_SHA256
        }
        if dict(room_model) != expected_room_model:
            raise ValueError(
                "basis manifest room model is not the current production authority."
            )
        envelope_id, footprint_x, footprint_y = mechanical_envelope(
            proposed_layout_mode
        )
        expected_asset_set = (
            "proposed-led-module-v1"
            if proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
            else "historical-proposed-fixture-assets-v1"
        )
        raw_envelope = (
            composition.get("mechanical_envelope", {})
        )
        if not isinstance(raw_envelope, Mapping):
            raise ValueError("layout_composition mechanical_envelope is invalid.")
        if (
            proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
            and (
                set(raw_envelope)
                != {"id", "width_x_m", "height_y_m"}
                or "fixture_asset_set_id" not in composition
            )
        ):
            raise ValueError(
                "standalone layout_composition must explicitly authenticate "
                "its mechanical envelope and fixture asset set."
            )
        source = _mapping(payload.get("proposed_source", {}), "proposed_source")
        try:
            shape_raw = payload["matrix_shape"]
            shape = tuple(int(value) for value in shape_raw)
            options = tuple(str(value) for value in radiance["options"])
            return cls(
                room_length_m=float(room["length"]),
                room_width_m=float(room["width"]),
                room_height_m=float(room["height"]),
                sensor_count=int(payload["sensor_count"]),
                control_zone_count=int(payload["control_zone_count"]),
                matrix_shape=shape,  # type: ignore[arg-type]
                radiance_options=options,
                nthreads=int(radiance["nthreads"]),
                reference_watts=float(payload["reference_watts"]),
                layout_module_count=int(payload["layout_module_count"]),
                module_profile_id=str(payload["module_profile_id"]),
                ambient_cache_policy=str(radiance["ambient_cache_policy"]),
                proposed_layout_mode=proposed_layout_mode,
                proposed_ring_mode=proposed_ring_mode,
                module_pattern_id=module_pattern_id,
                fixture_policy_id=resolved_fixture_policy_id,
                mechanical_envelope_id=str(
                    raw_envelope.get("id", envelope_id)
                ),
                module_footprint_x_m=float(
                    raw_envelope.get("width_x_m", footprint_x)
                ),
                module_footprint_y_m=float(
                    raw_envelope.get("height_y_m", footprint_y)
                ),
                fixture_asset_set_id=str(
                    composition.get("fixture_asset_set_id", expected_asset_set)
                ),
                room_model_identity_sha256=str(
                    room_model["identity_sha256"]
                ),
                backend=str(payload["backend"]),
                scalar_ppfd_decode_policy=str(scalar["decode_policy"]),
                scalar_ppfd_transport_basis=str(scalar["transport_basis"]),
                emitter_source_sha256=_optional_string(hashes.get("source_sha256")),
                emitter_metadata_sha256=_optional_string(
                    hashes.get("metadata_sha256")
                ),
                room_text_sha256=_optional_string(
                    workspace_hashes.get("room_text_sha256")
                ),
                sensor_text_sha256=_optional_string(
                    workspace_hashes.get("sensor_text_sha256")
                ),
                emitter_text_sha256_by_control_zone=tuple(
                    str(value) for value in hashes.get("by_control_zone", ())
                ),
                command_policy_sha256=_optional_string(
                    workspace_hashes.get("command_policy_sha256")
                ),
                fixture_occlusion_identity=_optional_string(
                    _mapping(
                        payload.get("fixture_occlusion", {}),
                        "fixture_occlusion",
                    ).get("identity_sha256")
                ),
                fixture_occlusion_manifest_sha256=_optional_string(
                    _mapping(
                        payload.get("fixture_occlusion", {}),
                        "fixture_occlusion",
                    ).get("classification_manifest_sha256")
                ),
                proposed_source_mode=str(
                    source.get("source_mode", "native_smd")
                ),
                proposed_source_identity_sha256=_optional_string(
                    source.get("identity_sha256")
                ),
                angular_data_sha256=_optional_string(
                    source.get("angular_data_sha256")
                ),
                source_classification=_optional_string(
                    source.get("classification")
                ),
                emitter_shape=_optional_string(
                    _mapping(
                        source.get("emitter_geometry", {}),
                        "proposed_source.emitter_geometry",
                    ).get("shape")
                ),
                emitter_area_per_module_m2=_optional_float(
                    _mapping(
                        source.get("emitter_geometry", {}),
                        "proposed_source.emitter_geometry",
                    ).get("area_per_module_m2")
                ),
                emitter_diameter_m=_optional_float(
                    _mapping(
                        source.get("emitter_geometry", {}),
                        "proposed_source.emitter_geometry",
                    ).get("diameter_m")
                ),
                authenticated_ies_sha256=_optional_string(
                    source.get("authenticated_ies_sha256")
                ),
                normalized_angular_identity_sha256=_optional_string(
                    source.get("normalized_angular_identity_sha256")
                ),
                angular_normalization_policy=_optional_string(
                    source.get("angular_normalization_policy")
                ),
                completed_aperture_characterization_identity_sha256=(
                    _optional_string(
                        source.get(
                            "completed_aperture_characterization_identity_sha256"
                        )
                    )
                ),
                controlled_spd_identity_sha256=_optional_string(
                    source.get("controlled_spd_identity_sha256")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid basis manifest payload: {exc}") from exc


def legacy_manifest_control_zone_count_matches(
    manifest: BasisManifest, legacy_payload: Mapping[str, Any]
) -> bool:
    """Compare old variable/ring counts with the new control-zone contract."""

    try:
        n_vars = int(legacy_payload["n_vars"])
        n_rings = int(legacy_payload["n_rings"])
    except (KeyError, TypeError, ValueError):
        return False
    return n_vars == n_rings == manifest.control_zone_count


def _positive_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite and positive.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object.")
    return value


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
