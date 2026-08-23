"""Meters-native room, sensor-grid, and rectangular-layout geometry."""

from .active_domain import (
    ACTIVE_DOMAIN_POLICY_ID,
    AISLE_WIDTH_FT,
    AISLE_WIDTH_M,
    ActiveRoomDomain,
)
from .coordinate_frame import (
    ROOM_BOUNDS_TOLERANCE_M,
    ROOM_FRAME_POLICY_ID,
    RoomCoordinateFrame,
)
from .mounting import (
    INCH_TO_METERS,
    MOUNTING_HEIGHT_DEFINITION,
    MOUNTING_HEIGHT_SCHEMA_ID,
    MOUNTING_HEIGHT_SCHEMA_VERSION,
    MountingGeometry,
    canonical_inches_to_meters,
)
from .rect_layout import (
    RectLayout,
    RectLayoutPosition,
    RectLayoutSpec,
    axis_positions,
    generate_rect_layout,
)
from .room import (
    DEFAULT_ROOM_HEIGHT_M,
    FEET_TO_METERS,
    RoomDimensions,
    feet_to_meters,
    room_radiance_text,
    write_room_radiance,
)
from .sensor_grid import (
    AdaptiveSensorGrid,
    AdaptiveSensorGridPolicy,
    BASELINE_REFERENCE_PLANE_Z_M,
    SensorGridSpec,
    SensorPoint,
    build_adaptive_sensor_grid,
    format_rtrace_receivers,
    format_sensor_coordinates,
    generate_sensor_points,
    write_sensor_grid,
)

__all__ = [name for name in globals() if not name.startswith("_")]
