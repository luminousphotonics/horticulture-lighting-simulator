from __future__ import annotations

import math


def finite_translation_matrix(x_m: float, y_m: float, z_m: float) -> list[float]:
    """Return a column-major 4x4 transform with finite translation values."""
    values = [float(x_m), float(y_m), float(z_m)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Assembly transform coordinates must be finite.")
    return [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        values[0],
        values[1],
        values[2],
        1.0,
    ]

