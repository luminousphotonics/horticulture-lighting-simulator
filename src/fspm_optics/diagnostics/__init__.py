"""Read-only scientific diagnostics outside production execution paths."""

from fspm_optics.diagnostics.receiver_placement import (
    CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M,
    DIAGNOSTIC_SCHEMA_ID,
    DIAGNOSTIC_SCHEMA_VERSION,
    FLOAT_COMPARISON_TOLERANCE,
    ReceiverPlacementDiagnosticError,
    best_band_adaptive_candidate,
    best_global_rectilinear_candidate,
    build_receiver_placement_diagnostic,
    classify_carrier_side,
    closest_point_on_faces,
    closest_point_on_triangle,
    current_fixed_candidate,
    format_receiver_placement_diagnostic,
    measure_carrier_plane_bracketing,
    summarize_carrier_plane_bracketing,
)

__all__ = [
    "CARRIER_SIDE_CLASSIFICATION_TOLERANCE_M",
    "DIAGNOSTIC_SCHEMA_ID",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "FLOAT_COMPARISON_TOLERANCE",
    "ReceiverPlacementDiagnosticError",
    "best_band_adaptive_candidate",
    "best_global_rectilinear_candidate",
    "build_receiver_placement_diagnostic",
    "classify_carrier_side",
    "closest_point_on_faces",
    "closest_point_on_triangle",
    "current_fixed_candidate",
    "format_receiver_placement_diagnostic",
    "measure_carrier_plane_bracketing",
    "summarize_carrier_plane_bracketing",
]
