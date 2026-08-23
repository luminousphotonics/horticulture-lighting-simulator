# Proposed LED source control

Proposed LED runs expose two stable Stage A source-control modes:

- `basis_matrix_optimized` is the default. It traces isolated control-zone
  basis columns, solves the target-independent spatial schedule, and then
  applies one capped global target factor.
- `uniform_module_dimming` is selected by the **Disable Basis-Matrix Solver**
  control. It traces one complete Proposed scene with every module at the
  canonical 100 W reference power, then applies one capped global dimming
  factor to every module equally.

Uniform mode does not plan or execute basis columns and does not run a spatial,
ring-power, or control-zone optimizer. A feasible target uses
`target / complete-reference-field mean`; an infeasible target returns the
100 W/module complete-reference field. Total electrical power is module count
times effective watts per module, and completed-aperture emitted PPF is that
power times the Proposed completed-aperture PPE.

The mode is Proposed-only and is serialized as `proposed_control_mode`.
Omitting it preserves the historical optimized behavior. Stage B authenticates
and reuses the exact Stage A schedule and global factor; it does not solve a new
schedule.

## Module arrangement

Module arrangement is a separate Proposed-only topology choice serialized as
`proposed_ring_mode`. It does not expose or alter the server-selected
`proposed_layout_mode` physical-composition policy.

- `full` is the UI and request default.
- `reduced_one_ring` derives the effective topology order as
  `nominal_base_n - 1` and constructs square tiles, centered rectangular
  rings, connectors, extensions, fixtures, and control zones directly at that
  order.

For a 10 × 10 ft room, full mode contains 61 modules across zone sizes
5/8/12/16/20; reduced mode contains 41 modules across 5/8/12/16. For the
centered pure-rectangular 30 × 50 ft topology, reduction removes the actual
80-module outer perimeter and changes 791 modules to 711 rather than cropping
a square.

Natural-fit scaling keeps the layout centered and fits both modes to the same
available X/Y room envelope. Reduced spans therefore receive larger pitch
while wall margin, fixture clearance, module-envelope clearance, mounting
plane, and the rigid 150 × 150 mm module asset remain unchanged. Equal
geometric coverage does not mean equal photon output: no per-module
renormalization compensates for the removed modules.
