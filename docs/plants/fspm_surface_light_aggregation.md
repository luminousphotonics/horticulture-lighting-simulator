# FSPM surface-light aggregation

Phase 27G-C derives optical surface accounting from the immutable five-band
juvenile receiver artifacts. It does not execute Radiance, adjust a source,
apply a target/reference cap, normalize values for display, or reconstruct a
symmetry proxy.

For band `b`, patch `q`, and side `s`, the raw receiver value is the incident
photon-flux density `I_bqs`. The bound Stage B leaf material is the sole
coefficient authority:

- absorbed density: `alpha_b * I_bqs`
- transmitted density: `tau_b * I_bqs`
- reflected density: `rho_b * I_bqs`
- local closure: `I_bqs = absorbed + transmitted + reflected`

Each side's photon rate is its density multiplied by the patch's physical
one-sided area. Front and back are incident hemispheres of the same physical
patch, so that physical area is applied once to each side and is not doubled.
The combined patch rate is the front rate plus the back rate.

PAR is the ordered sum of blue, green, orange, and red. Far-red remains a
separate spectral group. Patch rates are summed in canonical identity order to
leaf, plant, and all-modeled-plant-surface room totals. Higher-level front,
back, and combined densities are rate sums divided by the sum of physical
one-sided patch areas; densities are never naively summed or averaged.

New exports use a compact receiver index plus a little-endian Float64 plant
origin table. Global identities are reconstructed from canonical local
topology, plant index, and exact integer equations. Legacy expanded v1
receiver identity files remain servable for already completed runs, but new
runs do not materialize them.

The patch, leaf, and plant publications are fixed-stride little-endian binary
tables. The room summary, aggregation metadata, and derivation graph are small
JSON documents. Promotion independently reconstructs the compact scene
artifacts and re-derives the three binary digests and room summary from the raw
band files. Any coefficient, raw hash, count, link, closure, conservation, or
artifact mismatch rejects the staged run before atomic promotion.

Phase 27G-D2 may consume the patch Float64 table only after revalidating this
complete artifact chain. It selects only incident or absorbed PAR fields,
excludes the separately preserved far-red fields, and emits a hash-bound
Float32 raw-q derivative with the unchanged channel order
`front_incident_par`, `back_incident_par`, `front_absorbed_par`, and
`back_absorbed_par`. Neither the authoritative Float64 table nor this Float32
artifact stores normalized `u` or `z` values.

For authenticated schema-v2 display metadata, the browser preserves the raw-q
array and derives one bounded same-sized display array. Only the front channels
use the canonical-local-patch equation
`u[p,k,m] = q[p,k,m] / (gamma[family,m,k] * R)`. Back channels retain the
scalar equation `z = q / (beta * R)`. Fixed-anchor color saturation and
clipping statistics are display metadata, not mutations of either raw
artifact. Front clipping areas use canonical one-sided patch areas; patch
counts and physical areas remain separate.
