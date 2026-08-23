# Proposed forward completed-aperture normalization

The Proposed production source is normalized at the completed fixture
aperture. Its scalar contract is:

```text
completed-aperture forward PPE = 2.6 umol/J
internal source PPE * deterministic forward transmission = 2.6 umol/J
```

This is an emitted-source scene boundary, before room or plant transport. It
does **not** mean that a finite receiver plane captures 2.6 micromoles per
electrical joule. Receiver coverage, room losses, fixture directionality, and
plant interception remain transport results and are not source-normalization
inputs.

The current authorities, rounded to the declared characterization precision,
are:

| Proposed source | Forward transmission | Internal PPE (µmol/J) |
| --- | ---: | ---: |
| Native SMD | 0.833143250 | 3.120711833 |
| COB source-shape surrogate | 0.841355347 | 3.090251948 |

Production uses deterministic far-field forward `rtrace -I+` integration of
the completed-aperture response. The correction is scalar: it changes the
pre-transport source amplitude and its identities, not the physical stack or
angular shape. The 120 mm native emitting window, 128 × 128 × 3 mm
nonabsorbing PMMA, 0.90 ideal-diffuse PTFE, opaque bezel, authenticated Citizen
IES/DAT, normalized Citizen angular identity, 22 mm COB LES, and COB angular
profile are unchanged.

## Retired backward diagnostic

`fspm_optics.fixtures.smd.aperture_ppe_calibration` retains the four-check
backward near-field `rfluxmtx` workflow only as a non-authoritative diagnostic.
Its schema is
`fspm-optics.backward-rfluxmtx-aperture-diagnostic` and its candidate cannot
update production constants or identities. The backward estimator was retired
as authority because it was source-dependent and did not converge for the
finite directional COB source.

The dry-run command writes the diagnostic scenes and plan without locating
Radiance:

```bash
.venv/bin/python -m \
  fspm_optics.fixtures.smd.aperture_ppe_calibration \
  --nproc 6 \
  --output-dir \
  .proposed-fixture-calibration/aperture-ppe-plan
```

`--execute` remains an explicit diagnostic action. It performs the analytic
`h=u` control and checks A, B, and C, then records a
`t_fixture_candidate`. That candidate is comparison evidence only. The
separate COB v1 backward characterization entry point fails closed and cannot
publish another production authority.

Angular-research calibrations that contain backward flux estimates require an
explicit non-authoritative opt-in. Their basis manifests are not compatible
with current production replay. Production native and COB paths resolve only
the frozen forward authority.

## Historical and Quality-result interpretation

Pre-correction artifacts remain evidence under their declared historical
source identities. Historical validators may authenticate their internal
files and hashes, but current resume, cache reuse, materialization, and replay
must reject them.

The corrected COB Quality estimates are scalar derivations from certified COB
run trees. Corrected native Quality estimates derive from historical metrics
without auditable native run trees. Neither set is a newly traced or
recertified Quality result, and neither should be reported with more precision
than the underlying characterization and stored evidence support.
