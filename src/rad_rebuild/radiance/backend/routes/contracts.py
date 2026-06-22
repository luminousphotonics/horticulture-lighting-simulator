from __future__ import annotations

from typing import Any

from rad_rebuild.radiance.backend.models import PublicErrorResponse


PUBLIC_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": PublicErrorResponse},
    403: {"model": PublicErrorResponse},
    404: {"model": PublicErrorResponse},
    409: {"model": PublicErrorResponse},
    413: {"model": PublicErrorResponse},
    422: {"model": PublicErrorResponse},
    429: {"model": PublicErrorResponse},
    500: {"model": PublicErrorResponse},
    503: {"model": PublicErrorResponse},
}
