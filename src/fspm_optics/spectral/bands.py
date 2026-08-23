"""Fixed wavelength bands for future scalar-to-banded transport work."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final

PAR_START_NM: Final = 400
PAR_END_NM_EXCLUSIVE: Final = 700


@dataclass(frozen=True, slots=True)
class SpectralBand:
    """One closed integer-bin wavelength band in nanometres."""

    band_id: str
    label: str
    start_nm: int
    end_nm: int

    def __post_init__(self) -> None:
        if not self.band_id or not self.band_id.replace("_", "").isalnum():
            raise ValueError("band_id must be a non-empty identifier.")
        if not self.label:
            raise ValueError("band label must be non-empty.")
        if (
            isinstance(self.start_nm, bool)
            or not isinstance(self.start_nm, int)
            or isinstance(self.end_nm, bool)
            or not isinstance(self.end_nm, int)
            or self.start_nm <= 0
            or self.end_nm < self.start_nm
        ):
            raise ValueError("band wavelength bounds must be positive ordered integers.")

    @property
    def end_nm_exclusive(self) -> int:
        return self.end_nm + 1

    def contains(self, wavelength_nm: float | int) -> bool:
        if isinstance(wavelength_nm, bool) or not isinstance(
            wavelength_nm, int | float
        ):
            return False
        wavelength = float(wavelength_nm)
        return (
            math.isfinite(wavelength)
            and self.start_nm <= wavelength < self.end_nm_exclusive
        )

    def to_dict(self) -> dict[str, str | int]:
        return {
            "band_id": self.band_id,
            "label": self.label,
            "start_nm": self.start_nm,
            "end_nm": self.end_nm,
            "interval": "inclusive_integer_bin",
        }


BLUE_BAND: Final = SpectralBand("blue", "Blue", 400, 499)
GREEN_BAND: Final = SpectralBand("green", "Green", 500, 599)
ORANGE_BAND: Final = SpectralBand("orange", "Orange", 600, 624)
RED_BAND: Final = SpectralBand("red", "Red", 625, 699)
FAR_RED_BAND: Final = SpectralBand("far_red", "Far-red", 700, 750)

FIXED_TRANSPORT_BANDS: Final[tuple[SpectralBand, ...]] = (
    BLUE_BAND,
    GREEN_BAND,
    ORANGE_BAND,
    RED_BAND,
    FAR_RED_BAND,
)
