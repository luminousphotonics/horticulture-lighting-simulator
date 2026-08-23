# Stage B FSPM Surface-Light Metrics

## Purpose

Stage B uses deterministic plant geometry as a multispectral receiver layer. Radiance evaluates incident photon flux at receiver samples on both sides of each leaf surface. The FSPM Optics Engine applies the authenticated leaf-optics profile and aggregates the results through this hierarchy:

    receiver patch → leaf → plant → crop

These metrics describe photon delivery to and absorption by the modeled crop. They are separate from Stage A spatial-uniformity metrics calculated over the plant-free horizontal reference plane.

## Fundamental quantities

For receiver patch `i`:

- `a_i` is its authenticated physical one-sided area in square metres.
- `q_incident,front,i` and `q_incident,back,i` are its front- and back-side incident PAR flux densities.
- `q_absorbed,front,i` and `q_absorbed,back,i` are its front- and back-side absorbed PAR flux densities after application of the spectral leaf-optics profile.

Combined patch flux densities are:

    q_incident,i = q_incident,front,i + q_incident,back,i

    q_absorbed,i = q_absorbed,front,i + q_absorbed,back,i

Corresponding photon rates are:

    Q_incident,i = q_incident,i × a_i

    Q_absorbed,i = q_absorbed,i × a_i

Physical one-sided area is applied exactly once. Combining front- and back-side flux does not double physical leaf area.

PAR includes the authenticated photosynthetically active spectral bands. Far-red is reported separately where available and is excluded from emitted PAR PPF, absorbed capture efficiency, and absorbed PAR per electrical watt.

## Modeled plants

The number of deterministic plant instances included in the Stage B receiver model.

Every receiver patch must belong to exactly one leaf and one plant.

## Physical one-sided leaf area

Total physical one-sided area represented by all receiver patches:

    A_crop = Σ a_i

Units: `m²`

The one-sided convention supplies the area denominator even though photon flux is evaluated on both leaf sides.

## PAR combined incident exposure

Crop-wide area-weighted mean combined incident PAR flux density:

    Q_incident = Σ Q_incident,i

    E_incident = Q_incident / A_crop

Units: `µmol/m²/s`

This describes mean PAR arriving at both sides of the modeled plant surfaces.

## PAR combined absorbed exposure

Crop-wide area-weighted mean combined absorbed PAR flux density:

    Q_absorbed = Σ Q_absorbed,i

    E_absorbed = Q_absorbed / A_crop

Units: `µmol/m²/s`

`Q_absorbed` is the total absorbed PAR photon rate in `µmol/s`. The UI reports area-normalized exposure, while the full-precision photon rate is retained in the machine-readable Stage B metrics.

The implementation validates the conservation identity:

    Q_absorbed = E_absorbed × A_crop

within the authorized numerical tolerance.

## Absorbed capture efficiency

Absorbed capture efficiency is the fraction of emitted PAR PPF ultimately absorbed by the modeled crop:

    η_absorbed = Q_absorbed / PPF_emitted

    absorbed capture efficiency (%) = η_absorbed × 100

Units: `%`

The denominator is authenticated full-precision emitted PAR PPF at the completed Stage A operating point. It is not nominal fixture output, ePAR, incident crop flux, or electrical power.

This is a system-level crop-capture metric. It incorporates room transport, source placement, photon interception, canopy geometry, self-shading, and leaf absorptance. It is not a measurement of leaf absorptance alone.

## Absorbed PAR per electrical watt

This metric describes how much electrical energy ultimately becomes PAR absorbed by the modeled crop:

    absorbed PAR per electrical watt =
        Q_absorbed / P_electrical

Units:

    µmol/s/W = µmol/J

The denominator is authenticated modeled electrical power at the completed operating point.

This metric is not luminaire PPE. It represents the complete chain:

    electrical power
    → emitted PAR PPF
    → scene transport
    → crop interception
    → spectral leaf absorption

Because source efficacy contributes to this metric, comparisons intended to isolate spatial-delivery effects should use matched PPE and appropriate spectral controls.

## Plant-to-plant absorbed-exposure CV

Receiver patches are first aggregated by plant identity. For plant `p`:

    Q_absorbed,p = Σ Q_absorbed,i for plant p

    A_p = Σ a_i for plant p

    E_p = Q_absorbed,p / A_p

Each plant is then treated as one observation. For `N` plants:

    mean_plant = Σ E_p / N

    σ_plant = sqrt[Σ(E_p - mean_plant)² / N]

    plant-to-plant CV (%) =
        σ_plant / mean_plant × 100

Population standard deviation is used rather than sample standard deviation.

A lower value indicates more consistent mean absorbed exposure among crop positions. Because each deterministic plant contains its own leaf orientations and self-shading, this is the strongest Stage B summary of crop-position consistency.

## Plant minimum / mean absorbed exposure

This lower-tail uniformity ratio compares the least-exposed plant with the crop-wide plant mean:

    plant minimum/mean =
        min(E_p) / mean_plant

Units: dimensionless ratio

Values closer to `1.0` indicate that the least-exposed plant remains close to the crop mean. This complements CV by identifying a weak crop position that could be obscured by the overall distribution.

## Leaf-to-leaf absorbed-exposure CV

Receiver patches are first aggregated by deterministic leaf-instance identity. For leaf `l`:

    Q_absorbed,l = Σ Q_absorbed,i for leaf l

    A_l = Σ a_i for leaf l

    E_l = Q_absorbed,l / A_l

Each leaf instance is then treated as one observation. For `L` leaves:

    mean_leaf = Σ E_l / L

    σ_leaf = sqrt[Σ(E_l - mean_leaf)² / L]

    leaf-to-leaf CV (%) =
        σ_leaf / mean_leaf × 100

Population standard deviation is used.

This metric describes organ-scale exposure variability. It includes lighting-field variation, leaf orientation, leaf position, mutual shading, and canopy structure. It should not be interpreted as a pure lighting-system uniformity metric by itself.

## Far-red combined exposure

Where the spectral model includes far-red, incident and absorbed far-red exposure use the same area-weighted front-plus-back aggregation.

Far-red remains separate from PAR-derived capture efficiency and absorbed PAR per electrical watt.

## Numerical and validation rules

All scientific metrics are calculated from full-precision Stage B data before UI formatting.

The UI displays:

- Exposure values to three decimal places.
- Capture efficiency to three decimal places.
- Absorbed PAR per electrical watt to three decimal places.
- CV values to three decimal places.
- Plant minimum/mean to three decimal places.

Successful publication requires:

- Finite and nonnegative patch fluxes and rates.
- Positive receiver, leaf, plant, and crop areas.
- Positive mean plant and leaf exposure.
- Positive emitted PAR PPF and modeled electrical power.
- Complete and unique patch ownership.
- Consistent patch, leaf, plant, and crop totals.
- Conservation between absorbed photon rate and area-normalized absorbed exposure.

Invalid inputs fail closed instead of producing zero, null, infinite, or `NaN` scientific metrics.

## Interpretation and comparison

Controlled lighting-system comparisons should hold the following constant where required by the research question:

- Room and crop geometry.
- Plant age and deterministic receiver geometry.
- Receiver sampling resolution.
- Leaf-optics profile.
- Spectral power distribution.
- Achieved Stage A mean PPFD.
- Simulation-quality settings.
- Luminaire PPE when isolating spatial-delivery effects.

Plant-to-plant CV and plant minimum/mean are the clearest Stage B measures of crop-position consistency. Leaf-to-leaf CV provides organ-scale information. Absorbed capture efficiency and absorbed PAR per electrical watt quantify crop-level photon utilization.

These are light-transport metrics. They are not predictions of photosynthesis, biomass, photoinhibition, morphology, or crop yield.

## Percentage comparison conventions

Directional percentage change relative to a comparator is:

    (Proposed - Comparator) / Comparator × 100

This supports language such as:

    The Proposed system delivered X% more absorbed PAR per electrical
    joule than the Conventional system.

Symmetric percentage difference is:

    |A - B| / [(A + B) / 2] × 100

Because it has no directional reference, it should be described specifically as the percentage difference between two values.

