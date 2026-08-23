"""Typed failures for the HPS measured-shape comparison source."""


class HpsSourceModelError(ValueError):
    """Base class for invalid HPS source-model inputs."""


class HpsResourceError(HpsSourceModelError):
    """A required packaged HPS resource is missing or byte-invalid."""


class HpsProfileError(HpsSourceModelError):
    """HPS provenance or modeled-comparison identity is inconsistent."""


class HpsPhotometryError(HpsSourceModelError):
    """HPS LM-63 data cannot satisfy the approved photometric contract."""


class HpsSourcePlanError(HpsSourceModelError):
    """A pure derived-IES or aperture source plan is invalid."""


class HpsLayoutError(HpsSourceModelError):
    """A HPS coverage-grid or mount request is invalid."""


class NoLegalHpsLayoutError(HpsLayoutError):
    """Selected fixed-pitch counts cannot satisfy the physical wall clearance."""
