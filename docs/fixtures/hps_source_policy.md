# HPS source-model policy

Phase 3 defines the frozen HPS source authority around the approved
source vendor HPS `hps_1000w_fixture` LM-63 asset. The IES supplies angular
shape and a flat 0.798576 x 0.603504 m luminous-opening footprint. Absolute PAR
output is the documented initial lamp PPF of 1,750 umol/s. Tested system input
is 1,045 W, so reported system PPE is `1750 / 1045 =
1.6746411483253588 umol/J`. The nominal 1,000 W lamp class is product
provenance only.

`hps_1000w_spd.csv` is the sole relative spectral-shape authority. Its
SHA-256 is
`d27fffb2410d30ed38c7fde6851563e0ddba6a8de824ecefe8c7624038e5e133`.
It contains exactly 401 ordered 1 nm samples from 380 through 780 nm under the
header `wavelength_nm,relative_spd`. SPD amplitude does not set power, PPF,
PPE, dimming, or angular distribution.

## Canonical LM-63 dimensions

LM-63 dimensions are decimal tokens. Imperial dimension tokens are multiplied
by the exact decimal feet-to-metres factor `0.3048` before conversion to the
public float representation. Metric tokens are converted directly from their
decimal spelling. Consequently, the approved `1.98`, `2.62`, and `0.00` ft
tokens and the metric derived IES both resolve exactly to the canonical public
values 0.603504, 0.798576, and 0.0 m. No tolerance or profile-identity weakening
is used.

## Type-C symmetry and flux contract

The declared C-planes 0, 22.5, 45, 67.5, and 90 degrees expand by LM-63
quadrant symmetry to the following source-plane mapping:

```text
0, 22.5, 45, 67.5, 90, 67.5, 45, 22.5,
0, 22.5, 45, 67.5, 90, 67.5, 45, 22.5, 0
```

The final 360-degree plane duplicates zero degrees and only closes the final of
16 horizontal cells. Each adjacent C/gamma cell is integrated with the same
contract used by the Conventional source:

```text
cell flux
  = mean(four corner candela values)
  * radians(delta C)
  * (cos(gamma_0) - cos(gamma_1))
```

For the approved HPS bytes, this produces an authoritative expanded/downward
diagnostic of `118057.25673187636 cd sr`; upward flux is zero. The former Phase
24 value `117927.390044` came from theta-space trapezoidal integration of
sampled `I(theta) * sin(theta)`, not the accepted solid-angle-coordinate cell
contract, and has been corrected in the audit.

The expanded table is divided uniformly by the authoritative downward
integral. The deterministic derived IES must re-integrate to one under the same
cell contract. LM-63 lumens, raw candela magnitude, and the relative SPD do not
set absolute PAR output. If an IES contains upward output, that fraction is
reported and excluded from the active downward distribution.

The full-output equal-grey carrier is constructed once at the source as
`1750 x 179 = 313250`. Equal-grey scalar results therefore decode directly as
PPFD. There is no post-trace conversion, result scaling, target-driven dimming,
PPE matching, peak cap, or output equalization.
