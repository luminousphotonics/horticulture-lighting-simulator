#!/usr/bin/env python3
"""
layout_generator.py

Standalone port of the "Tile -> Fill -> Connect" logic from the Flask app.
Generates module placements for arbitrary rectangular rooms.
"""

from math import ceil, floor, isfinite
import json
import sys

from rad_rebuild.radiance.engine.layout.domain import LayoutResult, layout_result_from_mapping

# --- Constants & Known Solutions ---
INF = sys.maxsize
MAX_LAYOUT_DIMENSION_FT = 10_000.0
KNOWN_SOLUTIONS = {
    1: [('reverse_L', 'o3'), ('reverse_L', 'o1')],
    2: [('linear3', 'o1'), ('linear3', 'o2'), ('linear3', 'o1'), ('linear3', 'o2')],
    3: [('L', 'o2'), ('L', 'o3'), ('L', 'o4'), ('L', 'o1')],
    4: [('linear3', 'o1'), ('linear3', 'o2'), ('L', 'o3'), ('reverse_L', 'o1'), ('linear3', 'o2'), ('linear3', 'o1')],
    5: [('L', 'o2'), ('linear3', 'o2'), ('linear3', 'o1'), ('linear3', 'o1'), ('linear4', 'o2'), ('reverse_L', 'o2'), ('linear3', 'o1')],
    6: [('L', 'o2'), ('linear4', 'o2'), ('linear4', 'o1'), ('linear3', 'o1'), ('linear4', 'o2'), ('linear3', 'o2'), ('linear3', 'o1'), ('linear3', 'o1')],
    7: [('linear4', 'o2'), ('linear4', 'o2'), ('linear4', 'o1'), ('linear4', 'o1'), ('linear4', 'o2'), ('linear4', 'o2'), ('linear4', 'o1'), ('linear4', 'o1')],
    8: [('linear4', 'o2'), ('linear4', 'o2'), ('L', 'o3'), ('linear4', 'o1'), ('reverse_L', 'o1'), ('linear4', 'o2'), ('linear4', 'o2'), ('linear4', 'o1'), ('linear4', 'o1')]
}

# --- Core Helper Functions ---

def get_n_from_dimensions(d_ft):
    """Calculate base ring index n from dimension in feet."""
    if d_ft <= 4:
        return 2
    return floor((d_ft - 4) / 2) + 2

def get_ring_positions(layer):
    """Get integer grid coordinates for a square ring l."""
    d = layer + 1
    positions = []
    # top-right quadrant
    for i in range(0, d + 1):
        positions.append((i, d - i))
    # bottom-right
    for j in range(1, d + 1):
        positions.append((d - j, -j))
    # bottom-left
    for k in range(1, d + 1):
        positions.append((-k, -d + k))
    # top-left
    for p in range(1, d + 1):
        positions.append((-d + p, p))
    # dedupe
    return list(dict.fromkeys(positions))

def get_ring_positions_rect(k, offset):
    """Get integer coordinates for a rectangular extension ring."""
    u_max = offset + k
    v_max = k
    left, top, right, bottom = [], [], [], []
    
    # scan left edge
    u = -u_max
    for v in range(-v_max, v_max + 1):
        if (u + v) % 2 == 0:
            left.append((u, v))
        
    # scan top edge
    v = v_max
    for u in range(-u_max + 1, u_max + 1):
        if (u + v) % 2 == 0:
            if (u, v) not in left:
                top.append((u, v))
            
    # scan right edge
    u = u_max
    for v in range(v_max - 1, -v_max - 1, -1):
        if (u + v) % 2 == 0:
            if (u, v) not in (left + top):
                right.append((u, v))

    # scan bottom edge
    v = -v_max
    for u in range(u_max - 1, -u_max - 1, -1):
        if (u + v) % 2 == 0:
            if (u, v) not in (left + top + right):
                bottom.append((u, v))
            
    ring_pos = left + top + right + bottom
    return ring_pos, len(left), len(top), len(right), len(bottom)

def get_min_fixtures(r, corner_set, known_l=None):
    """Dynamic programming to solve minimum fixture tiling for a ring of size r."""
    if known_l is not None and known_l in KNOWN_SOLUTIONS:
        return len(KNOWN_SOLUTIONS[known_l]), KNOWN_SOLUTIONS[known_l]
   
    max_size = 4
    extended = r + max_size
    DP = [INF] * (extended + 1)
    prev = [None] * (extended + 1)
    DP[r] = 0
    
    # Solve backwards
    for pos in range(r - 1, -1, -1):
        # Try linear2
        if pos + 2 <= extended:
            has_corner_middle = any((pos + j) % r in corner_set for j in range(1, 2))
            if not has_corner_middle:
                new_val = 1 + DP[pos + 2]
                if new_val < DP[pos]:
                    DP[pos] = new_val
                    prev[pos] = (2, 'linear2', 'o1')
        # Try linear3
        if pos + 3 <= extended:
            has_corner_middle = any((pos + j) % r in corner_set for j in range(1, 3))
            if not has_corner_middle:
                new_val = 1 + DP[pos + 3]
                if new_val < DP[pos]:
                    DP[pos] = new_val
                    prev[pos] = (3, 'linear3', 'o1')
        # Try linear4
        if pos + 4 <= extended:
            has_corner_middle = any((pos + j) % r in corner_set for j in range(1, 4))
            if not has_corner_middle:
                new_val = 1 + DP[pos + 4]
                if new_val < DP[pos]:
                    DP[pos] = new_val
                    prev[pos] = (4, 'linear4', 'o1')
        # Try L
        if pos + 4 <= extended:
            c = (pos + 1) % r
            m = (pos + 2) % r
            if c in corner_set and m not in corner_set:
                new_val = 1 + DP[pos + 4]
                if new_val < DP[pos]:
                    DP[pos] = new_val
                    prev[pos] = (4, 'L', 'o1')
        # Try Reverse L
        if pos + 4 <= extended:
            c = (pos + 2) % r
            m = (pos + 1) % r
            if c in corner_set and m not in corner_set:
                new_val = 1 + DP[pos + 4]
                if new_val < DP[pos]:
                    DP[pos] = new_val
                    prev[pos] = (4, 'reverse_L', 'o1')
   
    if DP[0] == INF:
        return INF, []
   
    modules = []
    pos = 0
    while pos < r and prev[pos] is not None:
        size, mtype, orient = prev[pos]
        modules.append((mtype, orient))
        pos += size
    return DP[0], modules

def get_module_cobs(r, modules, ring_pos):
    """Assign physical COB coordinates to abstract module definitions."""
    module_groups = []
    pos = 0
    used = set()
    for mtype, orient in modules:
        size = 0
        if mtype == 'linear2':
            size = 2
        elif mtype == 'linear3':
            size = 3
        else:
            size = 4
        
        indices = [(pos + k) % r for k in range(size)]
        
        if all(i not in used for i in indices):
            cobs = [ring_pos[i] for i in indices]
            module_groups.append((mtype, orient, cobs))
            used.update(indices)
            pos += size
            
    return module_groups

def get_central_positions(offset):
    positions = []
    for u in range(-offset, offset + 1):
        if (u + 0) % 2 == 0:
            positions.append((u, 0))
    return positions

def get_central_modules(offset):
    central_cobs = get_central_positions(offset)
    num_c = len(central_cobs)
    if num_c < 2:
        return []
    
    # Greedy filling logic from app.py
    number4 = num_c // 4
    rem = num_c % 4
    number3 = 0
    number2 = 0
    if rem == 1:
        if number4 >= 1:
            number4 -= 1
            number3 = 1
            number2 = 1
        else:
            number3 = 1
            number2 = num_c - 3
    elif rem == 2:
        if number4 >= 1:
            number4 -= 1
            number3 = 2
        else:
            number2 = 1
    elif rem == 3:
        number3 = 1
        
    central_modules = []
    pos = 0
    for _ in range(number4):
        cobs = central_cobs[pos:pos + 4]
        central_modules.append(('linear4', 'o2', cobs))
        pos += 4
    for _ in range(number3):
        cobs = central_cobs[pos:pos + 3]
        central_modules.append(('linear3', 'o2', cobs))
        pos += 3
    for _ in range(number2):
        cobs = central_cobs[pos:pos + 2]
        central_modules.append(('linear2', 'o2', cobs))
        pos += 2
    return central_modules

def get_extension_groups(base_n, num_steps, shift_x=0.0, shift_y=0.0):
    extension_groups = []
    for i in range(1, num_steps + 1):
        k = base_n + i
        lower = ceil(i / 2)
        upper = floor((2 * base_n + i) / 2)
        num_cobs = upper - lower + 1 if upper >= lower else 0
        if num_cobs < 2:
            continue
        
        # Greedy logic similar to central modules
        rem = num_cobs % 4
        number4 = num_cobs // 4
        number3 = 0
        number2 = 0
        if rem == 1:
            if number4 >= 1:
                number4 -= 1
                number3 = 1
                number2 = 1
            else:
                number3 = 1
                number2 = num_cobs - 3
        elif rem == 2:
            if number4 >= 1:
                number4 -= 1
                number3 = 2
            else:
                number2 = 1
        elif rem == 3:
            number3 = 1
            
        modules_for_column = []
        for _ in range(number4):
            modules_for_column.append(('linear4', 'o1'))
        for _ in range(number3):
            modules_for_column.append(('linear3', 'o1'))
        for _ in range(number2):
            modules_for_column.append(('linear2', 'o1'))
        
        cobs = [(float(x) + shift_x, float(k - x) + shift_y) for x in range(lower, upper + 1)]
        pos = 0
        for mtype, orient in modules_for_column:
            size = int(mtype[-1])
            group_cobs = cobs[pos:pos + size]
            extension_groups.append((mtype, orient, group_cobs))
            pos += size
            
    return extension_groups


def _dedupe_positions_in_order(points):
    ordered = []
    seen = set()
    for x, y in points:
        key = (round(float(x), 6), round(float(y), 6))
        if key in seen:
            continue
        seen.add(key)
        ordered.append((float(x), float(y)))
    return ordered


def _transform_rect_cobs(local_cobs, shift_u=0.0, shift_v=0.0):
    return [
        ((float(u) + shift_u + float(v) + shift_v) / 2.0,
         (float(u) + shift_u - (float(v) + shift_v)) / 2.0)
        for u, v in local_cobs
    ]


def _append_square_tile(module_groups, zone_groups, tile_index, base_n, layer_modules, sx=0.0, sy=0.0, zone_index=0):
    if base_n >= 2:
        centerpiece = [(sx, sy), (1 + sx, sy), (sx, 1 + sy), (-1 + sx, sy), (sx, -1 + sy)]
        module_groups.append(("centerpiece", "o1", centerpiece))
        zone_groups.append(
            {
                "zone": zone_index,
                "kind": "square_centerpiece",
                "tile_index": tile_index,
                "local_ring": 0,
                "points": centerpiece,
            }
        )
        zone_index += 1

    for layer in range(1, base_n):
        ring_pos = get_ring_positions(layer)
        ring_pos_shifted = [(xx + sx, yy + sy) for xx, yy in ring_pos]
        ring_groups = get_module_cobs(4 * (layer + 1), layer_modules[layer - 1], ring_pos_shifted)
        module_groups.extend(ring_groups)
        zone_groups.append(
            {
                "zone": zone_index,
                "kind": "square_ring",
                "tile_index": tile_index,
                "local_ring": layer,
                "points": ring_pos_shifted,
            }
        )
        zone_index += 1

    return zone_index


def _validate_dimension(value, field_name):
    try:
        dimension = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite positive number.") from exc
    if not isfinite(dimension) or dimension <= 0.0 or dimension > MAX_LAYOUT_DIMENSION_FT:
        raise ValueError(
            f"{field_name} must be finite, greater than zero, and no greater than "
            f"{MAX_LAYOUT_DIMENSION_FT:g}."
        )
    return dimension


def _generate_layout_mapping(length_ft, width_ft, base_n_override=None):
    """
    Generate the exact horticultural layout plus explicit solver/control zones.

    Zones follow the same discrete construction logic as the layout:
    - each square tile contributes one centerpiece zone plus one zone per square ring
    - each connector strip between square tiles is its own zone
    - each rectangular extension contributes one center-strip zone plus one zone per rect ring
    """
    min_dim = min(length_ft, width_ft)
    max_dim = max(length_ft, width_ft)

    if base_n_override is not None:
        base_n = max(1, int(base_n_override))
    else:
        base_n = get_n_from_dimensions(min_dim)

    c = 2.0
    unit = min_dim + c
    min_rect_long = min_dim + 4
    max_s = floor((max_dim + c) / unit)
    s = max_s
    found = False
    has_rect = False
    rect_long = 0

    while s >= 0 and not found:
        if s == 0:
            rect_long = max_dim
            found = True
            has_rect = True
        else:
            pure_length = min_dim * s + c * (s - 1)
            rem = max_dim - pure_length
            if rem == 0:
                found = True
                has_rect = False
            elif rem > c and (rem - c) >= min_rect_long:
                rect_long = rem - c
                found = True
                has_rect = True
            else:
                s -= 1

    layer_modules = []
    for layer in range(1, base_n):
        corners = {0, layer + 1, 2 * (layer + 1), 3 * (layer + 1)}
        _min_fix, modules = get_min_fixtures(4 * (layer + 1), corners, layer)
        layer_modules.append(modules)

    module_groups = []
    zone_groups = []
    shift_step = base_n + 1
    zone_index = 0
    rect_offset = 0

    for j in range(s):
        sx = j * shift_step
        sy = j * shift_step
        zone_index = _append_square_tile(
            module_groups,
            zone_groups,
            tile_index=j,
            base_n=base_n,
            layer_modules=layer_modules,
            sx=sx,
            sy=sy,
            zone_index=zone_index,
        )

    for m in range(s - 1):
        csx = m * shift_step
        csy = m * shift_step
        connector_groups = get_extension_groups(base_n, 1, csx, csy)
        module_groups.extend(connector_groups)
        connector_points = []
        for _mtype, _orient, cobs in connector_groups:
            connector_points.extend(cobs)
        zone_groups.append(
            {
                "zone": zone_index,
                "kind": "connector",
                "connector_index": m,
                "points": _dedupe_positions_in_order(connector_points),
            }
        )
        zone_index += 1

    if has_rect:
        a = get_n_from_dimensions(rect_long)
        offset = a - base_n
        if offset % 2 == 1:
            a -= 1
            offset -= 1
        rect_offset = offset

        if s > 0:
            last_csx = (s - 1) * shift_step
            last_csy = (s - 1) * shift_step
            last_connector_groups = get_extension_groups(base_n, 1, last_csx, last_csy)
            module_groups.extend(last_connector_groups)
            connector_points = []
            for _mtype, _orient, cobs in last_connector_groups:
                connector_points.extend(cobs)
            zone_groups.append(
                {
                    "zone": zone_index,
                    "kind": "connector",
                    "connector_index": s - 1,
                    "points": _dedupe_positions_in_order(connector_points),
                }
            )
            zone_index += 1

            last_connector_u = base_n + 1 + (s - 1) * 2 * (base_n + 1)
            shift_u = last_connector_u + 1 + (offset + base_n)
        else:
            shift_u = 0

        shift_v = 0
        rect_modules = []

        center_line = get_central_positions(offset)
        transformed_center_line = _transform_rect_cobs(center_line, shift_u=shift_u, shift_v=shift_v)
        if transformed_center_line:
            zone_groups.append(
                {
                    "zone": zone_index,
                    "kind": "rect_center",
                    "local_ring": 0,
                    "offset": offset,
                    "points": _dedupe_positions_in_order(transformed_center_line),
                }
            )
            zone_index += 1

        if offset == 0 and transformed_center_line:
            ring1_pos, _len_left, _len_top, _len_right, _len_bottom = get_ring_positions_rect(1, offset)
            transformed_ring1_points = _transform_rect_cobs(ring1_pos, shift_u=shift_u, shift_v=shift_v)
            rect_modules.append(("centerpiece", "o1", [transformed_center_line[0], *transformed_ring1_points]))
            center_xy = transformed_center_line[0]
        else:
            center_xy = None

        central_groups = get_central_modules(offset)
        transformed_center_groups = []
        for mtype, orient, local_cobs in central_groups:
            transformed_cobs = _transform_rect_cobs(local_cobs, shift_u=shift_u, shift_v=shift_v)
            transformed_center_groups.append((mtype, orient, transformed_cobs))
        rect_modules.extend(transformed_center_groups)

        for k in range(1, base_n + 1):
            ring_pos, len_left, len_top, len_right, len_bottom = get_ring_positions_rect(k, offset)
            transformed_ring_points = _transform_rect_cobs(ring_pos, shift_u=shift_u, shift_v=shift_v)
            zone_groups.append(
                {
                    "zone": zone_index,
                    "kind": "rect_ring",
                    "local_ring": k,
                    "offset": offset,
                    "points": _dedupe_positions_in_order(transformed_ring_points),
                }
            )
            zone_index += 1

            if offset == 0:
                if k == 1:
                    continue
                square_ring_pos = [
                    (float(x) + float(center_xy[0]), float(y) + float(center_xy[1]))
                    for x, y in get_ring_positions(k - 1)
                ]
                ring_groups = get_module_cobs(4 * k, layer_modules[k - 2], square_ring_pos)
                rect_modules.extend(ring_groups)
                continue

            r = len(ring_pos)
            corner_set = {0, len_left - 1, len_left + len_top - 1, len_left + len_top + len_right - 1}
            _min_fix, modules = get_min_fixtures(r, corner_set)
            ring_groups = get_module_cobs(r, modules, ring_pos)
            transformed_ring_groups = []
            for mtype, orient, local_cobs in ring_groups:
                transformed_cobs = _transform_rect_cobs(local_cobs, shift_u=shift_u, shift_v=shift_v)
                transformed_ring_groups.append((mtype, orient, transformed_cobs))
            rect_modules.extend(transformed_ring_groups)

        module_groups.extend(rect_modules)

    ordered_positions = []
    for zone in zone_groups:
        ordered_positions.extend(zone.get("points", []))
    all_positions = _dedupe_positions_in_order(ordered_positions)

    return {
        "all_positions": all_positions,
        "module_groups": module_groups,
        "zone_groups": zone_groups,
        "topology": {
            "base_n": int(base_n),
            "square_tile_count": int(s),
            "connector_count": int(max(0, s - 1) + (1 if has_rect and s > 0 else 0)),
            "has_rect_extension": bool(has_rect),
            "rect_long_ft": float(rect_long) if has_rect else 0.0,
            "rect_offset": int(rect_offset),
            "zone_count": int(len(zone_groups)),
        },
    }


def generate_layout_result(length_ft, width_ft, base_n_override=None) -> LayoutResult:
    length = _validate_dimension(length_ft, "length_ft")
    width = _validate_dimension(width_ft, "width_ft")
    return layout_result_from_mapping(_generate_layout_mapping(length, width, base_n_override=base_n_override))


def generate_layout_with_zones(length_ft, width_ft, base_n_override=None):
    """
    Generate the exact horticultural layout plus explicit solver/control zones.

    This compatibility wrapper preserves the historical dict-shaped payload.
    New code should prefer :func:`generate_layout_result`.
    """
    return generate_layout_result(length_ft, width_ft, base_n_override=base_n_override).as_legacy_dict(
        include_zones=True
    )


def generate_layout(length_ft, width_ft, base_n_override=None):
    """
    Main Entry Point.
    Returns:
       layout_data (dict): Contains 'all_positions' (list of (x,y) tuples) 
                           and 'module_groups' (list of tuples (mtype, orient, cobs)).
    """
    return generate_layout_result(length_ft, width_ft, base_n_override=base_n_override).as_legacy_dict(
        include_zones=False
    )

if __name__ == "__main__":
    # Simple CLI test
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("L", type=float)
    parser.add_argument("W", type=float)
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()
    
    data = generate_layout(args.L, args.W)
    if args.json:
        payload = {
            "length_ft": args.L,
            "width_ft": args.W,
            "module_count": len(data["all_positions"]),
            "all_positions": data["all_positions"],
        }
        if args.pretty:
            print(json.dumps(payload, indent=2))
        else:
            print(json.dumps(payload))
    else:
        print(f"Generated {len(data['all_positions'])} modules for {args.L}x{args.W} ft room.")
