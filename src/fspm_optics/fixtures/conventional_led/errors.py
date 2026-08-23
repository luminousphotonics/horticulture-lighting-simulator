"""Typed failures for the Conventional LED comparison source model."""


class ConventionalLedError(ValueError):
    """Base class for invalid Conventional LED scientific inputs."""


class ConventionalResourceError(ConventionalLedError):
    """A packaged Conventional source resource is missing or has changed bytes."""


class ConventionalProfileError(ConventionalLedError):
    """The declared comparison profile is incomplete or inconsistent."""


class Lm63ParseError(ConventionalLedError):
    """LM-63 text is malformed or outside the deliberately supported subset."""


class AngularNormalizationError(ConventionalLedError):
    """A validated angular table cannot form a unit-total distribution."""


class ConventionalSpdError(ConventionalLedError):
    """The approved relative SPD cannot form the declared spectral shape."""


class ConventionalLayoutError(ConventionalLedError):
    """A Conventional fixture layout or mount request is invalid."""


class NoLegalConventionalLayoutError(ConventionalLayoutError):
    """Even one fixed-orientation Conventional fixture cannot fit legally."""


class UnsupportedFixtureRotationError(ConventionalLayoutError):
    """A request would require unsupported speculative fixture rotation."""
