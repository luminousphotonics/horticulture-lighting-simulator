# Conventional fixture layout and mount policy

Phase 22B defines a pure planning boundary for the declared Conventional LED
comparator. It does not construct Radiance sources, scene files, commands, or
runtime workspaces.

## Fixed geometry and room axes

The approved LM-63 footprint is used without rescaling:

- length along aligned X: `1.190 m`
- width along aligned Y: `1.087 m`
- fixture height: `0.108 m`

The longer requested floor dimension is aligned to X. If the requested width is
longer than the requested length, the room axes are swapped as a pair; the
fixture is not rotated independently. Equal dimensions remain unswapped, making
the square-room rule deterministic. Plans retain requested dimensions, aligned
dimensions, the swap decision, and both aligned and requested coordinates and
bounds for every fixture.

Nonzero fixture rotation is unsupported and is rejected. A plan is also rejected
if the fixed footprint cannot fit inside the room after the declared margins.
Positions are never clamped, fixture geometry is never rescaled, and counts are
never reduced after resolution.

The named `rolling_bench` mode is the sole explicit orientation exception. The
fixture is fixed at 90 degrees in the aligned simulation frame, so its physical
`1.087 m` width is the aligned X extent and its physical `1.190 m` length is the
aligned Y extent. This is an authored mode orientation, not automatic room-fit
rotation.

## Required `practical` policy

`practical` is the default and required controlled-comparison policy. Its
cross-system coupling is intentional and is present in its types and serialized
artifacts:

1. Generate the validated Proposed SMD exact-tiled layout for the aligned room.
2. Resolve its X and Y pitches independently.
3. Subtract the Proposed SMD module footprint on the corresponding axis, with a
   lower bound of zero, to obtain the Conventional target practical gap.
4. For each aligned axis independently, resolve the Conventional count as
   `floor(max(usable - target_gap, fixture_span) / (fixture_span + target_gap))`,
   with a minimum of one only after proving that one complete fixture fits.
5. Redistribute remaining free space as `free / (count + 1)`. This produces an
   equal free gap between neighboring footprints and just inside both fixed wall
   margins.

The wall margin defaults to exactly one inch, converted at the API boundary as
`0.0254 m` per wall. Fixture instances are ordered Y-major, with X increasing
within each row.

For a 10 ft by 10 ft room, `practical` resolves two columns and two rows. The
target gaps are approximately `5.15 in` on X and `4.60433 in` on Y. With the
approved metric fixture dimensions, the actual equal centered gaps are
`0.2057333333 m` on X and `0.2744 m` on Y, producing aligned centers near
`(±0.6978666667, ±0.6807) m`.

## Optional `full_fit` policy

`full_fit` is a separately named diagnostic policy. It floors the usable span by
the complete fixture footprint independently on each axis, then uses the same
centering and geometry checks. It has no Proposed SMD pitch or target-gap fields
and never becomes the implicit comparison policy.

## Optional `rolling_bench` policy

`rolling_bench` models closed production benches with no internal aisle and no
excluded receiver area. It uses the complete aligned room dimensions, a rigid
`48 in = 1.2192 m` center-to-center pitch on both axes, and no additional wall
margin. For each room span `D` and oriented fixture span `F`, it requires
`D >= F`, resolves `floor((D - F) / 1.2192) + 1` fixtures, and centers the
resulting rigid span without stretching pitch. The resulting fixture-to-fixture
edge gaps are `0.1322 m` on aligned X (`5.205 in` rounded to three decimals) and
`0.0292 m` on aligned Y (`1.150 in` rounded to three decimals).

The longer requested room dimension remains aligned to X under the shared room
coordinate-frame policy. Consequently both `24 ft × 12 ft` and its portrait
request equivalent resolve six aligned-X columns and three aligned-Y rows. The
room, reference plane, sensor grid, receiver spacing, and evaluated domain are
unchanged; only Conventional fixture coordinates differ.

## Mount and transform semantics

`mount_height_m = 0.4572` is measured from the emitting aperture plane to the
receiver/reference plane. With the default reference plane at `z = 0.0 m`:

- aperture Z is `0.0 + 0.4572 = 0.4572 m`
- fixture-center Z is `0.4572 + 0.108 / 2 = 0.5112 m`

A nonzero reference-plane Z translates both values without redefining mount
height. Each instance records a pure transform with local emission along
negative Z and world direction `(0, 0, -1)`.

## Public planning API

`plan_conventional_layout(room_length_m, room_width_m, ...)` accepts metres.
`plan_conventional_layout_from_feet(room_length_ft, room_width_ft, ...)` is the
explicit legacy-unit boundary. Both return an immutable
`ConventionalLayoutPlan` with deterministic JSON and a SHA-256-derived layout
identity. Identity input includes the Conventional profile, policy, requested and
aligned rooms, margins, fixed dimensions, target and actual gaps, resolved
counts, mount/reference-plane definition, and ordered fixture instances. It does
not include local filesystem paths.
