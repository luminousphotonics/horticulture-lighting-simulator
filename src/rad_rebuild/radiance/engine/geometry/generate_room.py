#!/usr/bin/env python3
"""
generate_room.py

Emit a Radiance room definition whose footprint is controlled by
environment variables:

  LENGTH_M / LENGTH_FT  – room X-extent (meters)
  WIDTH_M  / WIDTH_FT   – room Y-extent (meters)
  HEIGHT_M             – room Z-extent (meters)

Defaults reproduce your original 12' x 12' x 10' room:

  LENGTH_M = WIDTH_M = 3.6576
  HEIGHT_M = 3.048
"""

import os

FT_TO_M = 0.3048


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _maybe_swap_dims(L: float, W: float) -> tuple[float, float]:
    align = os.getenv("ALIGN_LONG_AXIS_X", "1") == "1"
    if align and W > L:
        return W, L
    return L, W


# Defaults: 12' x 12' x 10'
L = _f("LENGTH_M", _f("LENGTH_FT", 12.0) * FT_TO_M)  # X dimension (m)
W = _f("WIDTH_M", _f("WIDTH_FT", 12.0) * FT_TO_M)  # Y dimension (m)
L, W = _maybe_swap_dims(L, W)
H = _f("HEIGHT_M", 3.048)  # Z dimension (m)

hx = L / 2.0
hy = W / 2.0

room = f"""void plastic white_mylar
0
0
5 0.90 0.90 0.90  0.0 0.02

void plastic ceiling_mylar
0
0
5 0.90 0.90 0.90  0.0 0.02

void plastic floor_mylar
0
0
5 0.10 0.10 0.10  0.0 0.02

floor_mylar polygon floor
0
0
12
{-hx:.4f} {-hy:.4f} 0
{hx:.4f} {-hy:.4f} 0
{hx:.4f} {hy:.4f} 0
{-hx:.4f} {hy:.4f} 0

ceiling_mylar polygon ceiling
0
0
12
{-hx:.4f} {-hy:.4f} {H:.4f}
{-hx:.4f} {hy:.4f} {H:.4f}
{hx:.4f} {hy:.4f} {H:.4f}
{hx:.4f} {-hy:.4f} {H:.4f}

white_mylar polygon wall_neg_x
0
0
12
{-hx:.4f} {-hy:.4f} 0
{-hx:.4f} {hy:.4f} 0
{-hx:.4f} {hy:.4f} {H:.4f}
{-hx:.4f} {-hy:.4f} {H:.4f}

white_mylar polygon wall_pos_x
0
0
12
{hx:.4f} {-hy:.4f} 0
{hx:.4f} {-hy:.4f} {H:.4f}
{hx:.4f} {hy:.4f} {H:.4f}
{hx:.4f} {hy:.4f} 0

white_mylar polygon wall_neg_y
0
0
12
{-hx:.4f} {-hy:.4f} 0
{-hx:.4f} {-hy:.4f} {H:.4f}
{hx:.4f} {-hy:.4f} {H:.4f}
{hx:.4f} {-hy:.4f} 0

white_mylar polygon wall_pos_y
0
0
12
{-hx:.4f} {hy:.4f} 0
{hx:.4f} {hy:.4f} 0
{hx:.4f} {hy:.4f} {H:.4f}
{-hx:.4f} {hy:.4f} {H:.4f}
"""

print(room, end="")
