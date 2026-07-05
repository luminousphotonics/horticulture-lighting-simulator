# FSPM Plant Modeling Feature Plan

## Goal

Add functional structural plant modeling to the horticulture lighting simulator so plant geometry can be included in Radiance simulations and visualized in the 3D viewer.

## Initial scope

- Add procedural leafy-green plant geometry.
- Export matching geometry for Radiance and the browser viewer.
- Support deterministic random seeds.
- Compare PPFD results with and without plant geometry.
- Keep the feature experimental until validated.

## First MVP crop

Leafy green / lettuce-style rosette crop.

## Initial plant parameters

- plant spacing
- plant height
- canopy radius
- leaf count
- leaf size distribution
- leaf tilt distribution
- growth stage
- random seed
- material reflectance/transmittance assumptions

## First validation target

Compare no-plant baseline against simple plant geometry and report:

- mean PPFD change
- min/max change
- CV change
- DOU change
- difference map
