#!/usr/bin/env python3
# solve_uniformity.py
# Compute per-ring powers w for target PPFD using basis_A and a smoothed LSQ.

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, cast

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import Bounds, LinearConstraint, lsq_linear, linprog, minimize  # type: ignore[import-untyped]

from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    format_ppfd_metrics_line,
)
from rad_rebuild.radiance.engine.photometry.smd_curve_model import build_smd_curve_model
from rad_rebuild.radiance.engine.emitters.smd_generation.solution_metadata import (
    build_smd_runtime_fingerprint_from_env,
    build_smd_solution_metadata,
)

FloatArray = NDArray[np.float64]
JsonObject = dict[str, Any]
SolverScore = tuple[float, ...]
SolverChoice = tuple[object, object | None]
BestSolution = tuple[SolverScore, SolverChoice, FloatArray, JsonObject]
SolverLogger = Callable[..., None]


@dataclass(frozen=True)
class SolverInputs:
    basis_path: Path
    matrix: FloatArray
    manifest: JsonObject | None
    transform: "BasisSolveTransform"
    n_points: int
    n_variables: int


@dataclass(frozen=True)
class SolverVariableMetadata:
    var_mode: str | None
    ring_group: int | None
    outer_indices: list[Any]
    outer_ring_index: Any
    ring_indices: Any
    module_indices: Any

    @property
    def per_module_mode(self) -> bool:
        return self.var_mode == "per_module"


@dataclass(frozen=True)
class SolverLambdaSettings:
    lambdas_s: list[float]
    lambdas_r: list[float]
    lambda_smooth: float
    ridge_w: FloatArray
    smooth_groups: list[int] | None


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Ring-wise Radiance photonic density uniformity solver"
    )
    ap.add_argument(
        "--solve-method",
        default="minvar_qp",
        choices=["minvar_qp", "lsq"],
        help="Solver strategy: exact min-variance QP or legacy LSQ sweep",
    )
    ap.add_argument(
        "--basis", default="basis_A.npy", help="Basis matrix file (npy or csv)"
    )
    ap.add_argument(
        "--target-ppfd", type=float, default=1200.0, help="Target mean PPFD (µmol/m²/s)"
    )
    ap.add_argument(
        "--w-min",
        type=float,
        default=0.0,
        help="Lower bound on per-ring power scale (W per module)",
    )
    ap.add_argument(
        "--w-max",
        type=float,
        default=100.0,
        help="Upper bound on per-ring power scale (W per module)",
    )
    ap.add_argument(
        "--lambda-s",
        type=float,
        nargs="+",
        default=[0.0, 1e-3, 1e-2, 1e-1, 1.0, 10.0, 30.0],
        help="Smoothing weights to try (L2 on ring differences)",
    )
    ap.add_argument(
        "--lambda-smooth",
        type=float,
        default=0.0,
        help="Smoothness weight for minvar_qp (L2 on ring differences)",
    )
    ap.add_argument(
        "--lambda-r",
        type=float,
        nargs="+",
        default=[0.0, 1e-3, 1e-2, 1e-1],
        help="Ridge weights to try (L2 on absolute ring powers)",
    )
    ap.add_argument(
        "--ridge-weights",
        type=float,
        nargs="*",
        default=None,
        help="Optional per-variable ridge weights (len=K, or a single scalar to broadcast).",
    )
    ap.add_argument(
        "--lambda-mean",
        type=float,
        default=10.0,
        help="Weight on enforcing the target mean PPFD",
    )
    ap.add_argument(
        "--smooth-groups",
        type=int,
        nargs="*",
        default=None,
        help="Optional group sizes for smoothing (build D per group, e.g. '13 13' for 2 channels).",
    )
    ap.add_argument(
        "--use-chebyshev",
        action="store_true",
        default=False,
        help="Also try a Chebyshev (minimax) solve to shrink max error",
    )
    ap.add_argument(
        "--tol-mean",
        type=float,
        default=0.005,
        help="Relative tolerance on mean PPFD after rescale (0.005 = 0.5%)",
    )
    ap.add_argument(
        "--out-json",
        default="ring_powers_optimized.json",
        help="Output JSON with ring powers and metrics",
    )
    ap.add_argument(
        "--legacy-metrics",
        action="store_true",
        default=False,
        help="Also compute/log legacy std/CV/DOU metrics",
    )
    return ap.parse_args()


@dataclass(frozen=True)
class UniformitySolverConfig:
    """Validated CLI boundary config for the SMD basis uniformity solver.

    The numerical problem minimizes spatial PPFD variance around the target
    mean.  The basis matrix has shape `(n_sensor_points, n_variables)` and maps
    source coefficients to PPFD samples.  Bounds are expressed as electrical
    watts/module at the CLI boundary, then converted to basis coefficients when
    the curve source model is active.  Solves are deterministic for fixed basis,
    manifest, environment adapter, and scipy/numpy versions.
    """

    solve_method: str
    basis_path: Path
    target_ppfd: float
    w_min: float
    w_max: float
    lambda_s: tuple[float, ...]
    lambda_smooth: float
    lambda_r: tuple[float, ...]
    ridge_weights: tuple[float, ...] | None
    lambda_mean: float
    smooth_groups: tuple[int, ...] | None
    use_chebyshev: bool
    tol_mean: float
    out_json: Path
    legacy_metrics: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "UniformitySolverConfig":
        return cls(
            solve_method=str(args.solve_method),
            basis_path=Path(args.basis),
            target_ppfd=float(args.target_ppfd),
            w_min=float(args.w_min),
            w_max=float(args.w_max),
            lambda_s=tuple(float(value) for value in args.lambda_s),
            lambda_smooth=float(args.lambda_smooth),
            lambda_r=tuple(float(value) for value in args.lambda_r),
            ridge_weights=None
            if args.ridge_weights is None
            else tuple(float(value) for value in args.ridge_weights),
            lambda_mean=float(args.lambda_mean),
            smooth_groups=None
            if args.smooth_groups is None
            else tuple(int(value) for value in args.smooth_groups),
            use_chebyshev=bool(args.use_chebyshev),
            tol_mean=float(args.tol_mean),
            out_json=Path(args.out_json),
            legacy_metrics=bool(args.legacy_metrics),
        )


@dataclass(frozen=True)
class SolverEvent:
    level: str
    message: str


@dataclass(frozen=True)
class UniformitySolverResult:
    output_path: Path
    output_payload: JsonObject
    events: tuple[SolverEvent, ...]
    n_points: int
    n_variables: int


def load_basis(path: Path) -> FloatArray:
    if path.suffix == ".npy":
        return cast(FloatArray, np.load(path))
    # assume csv/tsv
    return np.loadtxt(path, delimiter=",")


def validate_basis_matrix(A: FloatArray) -> FloatArray:
    matrix = np.asarray(A, dtype=float)
    if matrix.ndim != 2:
        raise SystemExit(f"Basis matrix must be 2-D, got shape {matrix.shape}.")
    n_points, n_vars = matrix.shape
    if n_points <= 0 or n_vars <= 0:
        raise SystemExit(f"Basis matrix must be non-empty, got shape {matrix.shape}.")
    if not np.all(np.isfinite(matrix)):
        raise SystemExit("Basis matrix contains non-finite values.")
    return cast(FloatArray, matrix)


def load_basis_manifest(
    basis_path: Path,
    *,
    runtime_env: Mapping[str, Any] | None = None,
) -> JsonObject | None:
    # Prefer explicit BASIS_MANIFEST if set; otherwise look next to basis file.
    env_source = _stringify_env(runtime_env) or _stringify_env(os.environ)
    manifest_env = env_source.get("BASIS_MANIFEST", "").strip()
    if manifest_env:
        p = Path(manifest_env)
    else:
        p = basis_path.parent / "basis_manifest.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return cast(JsonObject, data)


def _stringify_env(source: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(source, Mapping):
        return {}
    return {
        str(key): "" if value is None else str(value) for key, value in source.items()
    }


@dataclass(frozen=True)
class BasisSolveTransform:
    solve_space: str
    basis_unit_w_per_module: float
    basis_source_photon_umol_s: float
    user_eff_scale: float
    model: Any | None = None

    @property
    def is_curve(self) -> bool:
        return self.solve_space == "curve_source_photon_scale"

    def _evaluated_input_w(self, scheduled_w: float) -> float:
        return max(0.0, float(scheduled_w) * self.user_eff_scale)

    def _source_photon_umol_s(self, scheduled_w: float) -> float:
        if not self.is_curve or self.model is None:
            return max(0.0, float(scheduled_w))
        state = self.model.evaluate_module(
            self._evaluated_input_w(scheduled_w), pmma_active=True
        )
        return float(state.source_photon_umol_s)

    def coeff_from_weight(self, scheduled_w: float) -> float:
        if not self.is_curve:
            return float(scheduled_w)
        if self.basis_source_photon_umol_s <= 1e-12:
            raise SystemExit(
                "Curve solve basis source photon flux is zero; rebuild the SMD basis."
            )
        return self._source_photon_umol_s(scheduled_w) / self.basis_source_photon_umol_s

    def coeffs_from_weights(self, weights: FloatArray) -> FloatArray:
        if not self.is_curve:
            return cast(FloatArray, np.asarray(weights, dtype=float))
        arr = np.asarray(weights, dtype=float)
        return cast(
            FloatArray,
            np.asarray([self.coeff_from_weight(float(w)) for w in arr], dtype=float),
        )

    def coeff_bounds(self, w_min: float, w_max: float) -> tuple[float, float]:
        if not self.is_curve:
            return float(w_min), float(w_max)
        q0 = self.coeff_from_weight(w_min)
        q1 = self.coeff_from_weight(w_max)
        return (min(q0, q1), max(q0, q1))

    def weight_from_coeff(self, coeff: float, *, w_min: float, w_max: float) -> float:
        if not self.is_curve:
            return float(coeff)
        lo = float(w_min)
        hi = float(w_max)
        target_coeff = float(coeff)
        lo_coeff = self.coeff_from_weight(lo)
        hi_coeff = self.coeff_from_weight(hi)
        if target_coeff <= lo_coeff + 1e-12:
            return lo
        if target_coeff >= hi_coeff - 1e-12:
            return hi
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            mid_coeff = self.coeff_from_weight(mid)
            if mid_coeff < target_coeff:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def weights_from_coeffs(
        self, coeffs: FloatArray, *, w_min: float, w_max: float
    ) -> FloatArray:
        if not self.is_curve:
            return cast(FloatArray, np.asarray(coeffs, dtype=float))
        arr = np.asarray(coeffs, dtype=float)
        return cast(
            FloatArray,
            np.asarray(
                [
                    self.weight_from_coeff(float(q), w_min=w_min, w_max=w_max)
                    for q in arr
                ],
                dtype=float,
            ),
        )


def build_basis_solve_transform(
    *,
    basis_manifest: dict[str, Any] | None = None,
    runtime_env: Mapping[str, Any] | None = None,
) -> BasisSolveTransform:
    manifest_env = _stringify_env((basis_manifest or {}).get("emitter_env"))
    env_source = (
        manifest_env or _stringify_env(runtime_env) or _stringify_env(os.environ)
    )
    smd_model = str(env_source.get("SMD_MODEL", "curve")).strip().lower() or "curve"
    if smd_model == "legacy":
        return BasisSolveTransform(
            solve_space="electrical_w_per_module",
            basis_unit_w_per_module=float(
                (basis_manifest or {}).get("basis_unit_w_per_module", 1.0) or 1.0
            ),
            basis_source_photon_umol_s=0.0,
            user_eff_scale=float(env_source.get("EFF_SCALE", "1.0") or "1.0"),
            model=None,
        )

    basis_unit_w = float(
        (basis_manifest or {}).get("basis_unit_w_per_module", 1.0) or 1.0
    )
    user_eff_scale = float(env_source.get("EFF_SCALE", "1.0") or "1.0")
    curve_model = build_smd_curve_model(env=env_source)
    basis_state = curve_model.evaluate_module(
        max(0.0, basis_unit_w * user_eff_scale), pmma_active=True
    )
    basis_source = float(basis_state.source_photon_umol_s)
    if basis_source <= 1e-12:
        raise SystemExit(
            "Curve solve basis source photon flux is zero; rebuild the SMD basis."
        )
    return BasisSolveTransform(
        solve_space="curve_source_photon_scale",
        basis_unit_w_per_module=basis_unit_w,
        basis_source_photon_umol_s=basis_source,
        user_eff_scale=user_eff_scale,
        model=curve_model,
    )


def solution_coefficients_from_json(
    data: Mapping[str, Any],
    *,
    transform: BasisSolveTransform | None = None,
) -> FloatArray:
    coeffs = data.get("basis_coefficients")
    if coeffs is not None:
        return cast(FloatArray, np.asarray(coeffs, dtype=float))
    weights = None
    if data.get("module_powers_W_per_module") is not None:
        weights = cast(
            FloatArray, np.asarray(data.get("module_powers_W_per_module"), dtype=float)
        )
    else:
        ring = data.get("ring_powers_W_per_module") or data.get("ring_powers") or []
        outer = (
            data.get("outer_ring_powers_W_per_module")
            or data.get("outer_ring_powers")
            or []
        )
        weights = cast(FloatArray, np.asarray(list(ring) + list(outer), dtype=float))
    if transform is None:
        return weights
    return transform.coeffs_from_weights(weights)


def field_from_weights(
    A: FloatArray, weights: FloatArray, *, transform: BasisSolveTransform | None = None
) -> FloatArray:
    coeffs = (
        cast(FloatArray, np.asarray(weights, dtype=float))
        if transform is None
        else transform.coeffs_from_weights(weights)
    )
    return cast(FloatArray, A @ coeffs)


def build_D(K: int) -> FloatArray:
    """Finite difference matrix for ring-to-ring smoothing: (K-1)×K."""
    D = np.zeros((K - 1, K), dtype=float)
    for i in range(K - 1):
        D[i, i] = 1.0
        D[i, i + 1] = -1.0
    return cast(FloatArray, D)


def build_D_groups(groups: list[int], K: int) -> FloatArray:
    """Block-diagonal D built per group (each group uses first differences)."""
    if not groups:
        return build_D(K)
    if any(int(g) < 0 for g in groups):
        raise ValueError("smooth group sizes must be non-negative")
    if sum(groups) != K:
        raise ValueError(f"smooth groups sum={sum(groups)} must equal K={K}")

    n_rows = sum(max(0, int(g) - 1) for g in groups)
    D = np.zeros((n_rows, K), dtype=float)
    row0 = 0
    col0 = 0
    for g in groups:
        g = int(g)
        if g >= 2:
            for i in range(g - 1):
                D[row0 + i, col0 + i] = 1.0
                D[row0 + i, col0 + i + 1] = -1.0
            row0 += g - 1
        col0 += g
    return cast(FloatArray, D)


def metrics_from_field(
    p: FloatArray, *, setpoint_ppfd: float | None = None, legacy_metrics: bool = False
) -> JsonObject:
    return cast(
        JsonObject,
        compute_ppfd_metrics(
            p, setpoint_ppfd=setpoint_ppfd, legacy_metrics=legacy_metrics
        ),
    )


def _cap_metrics_enabled(runtime_env: Mapping[str, Any] | None) -> bool:
    if runtime_env is None:
        return False
    raw = str(runtime_env.get("LOG_CAP_METRICS", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _mean_range(
    mean_row: FloatArray, w_min: float, w_max: float
) -> tuple[float | None, float | None]:
    """Compute achievable mean range under bounds using LP."""
    K = int(mean_row.shape[1])
    c = mean_row.reshape(-1).astype(float)
    bounds = [(w_min, w_max)] * K
    res_min = linprog(c, bounds=bounds, method="highs")
    res_max = linprog(-c, bounds=bounds, method="highs")
    min_mean = float(res_min.fun) if res_min.success else None
    max_mean = float(-res_max.fun) if res_max.success else None
    return min_mean, max_mean


def _find_feasible_mean_point(
    mean_row: FloatArray, target_mu: float, w_min: float, w_max: float
) -> FloatArray | None:
    """Find any w satisfying mean constraint and bounds, or None if infeasible."""
    K = int(mean_row.shape[1])
    c = np.zeros((K,), dtype=float)
    bounds = [(w_min, w_max)] * K
    res = linprog(c, A_eq=mean_row, b_eq=[target_mu], bounds=bounds, method="highs")
    if res.success:
        return cast(FloatArray, res.x)
    return None


def solve_minvar_qp(
    A: FloatArray,
    target_mu: float,
    w_min: float,
    w_max: float,
    *,
    lambda_smooth: float = 0.0,
    smooth_groups: list[int] | None = None,
    legacy_metrics: bool = False,
    tol_mean: float = 0.005,
    basis_manifest: JsonObject | None = None,
    runtime_env: Mapping[str, Any] | None = None,
) -> tuple[FloatArray, JsonObject, JsonObject]:
    """Exact min-variance solution: minimize ||A w - mu||^2 with mean equality + bounds."""
    A = validate_basis_matrix(A)
    n_points, K = A.shape
    transform = build_basis_solve_transform(
        basis_manifest=basis_manifest, runtime_env=runtime_env
    )
    ones = np.ones((n_points,), dtype=float)
    b = target_mu * ones
    mean_row = cast(FloatArray, A.mean(axis=0, keepdims=True))  # shape (1, K)
    coeff_min, coeff_max = transform.coeff_bounds(w_min, w_max)

    x0 = _find_feasible_mean_point(mean_row, target_mu, coeff_min, coeff_max)
    if x0 is None:
        min_mean, max_mean = _mean_range(mean_row, coeff_min, coeff_max)
        raise SystemExit(
            f"Target mean {target_mu:.3f} is infeasible with bounds "
            f"[{w_min:.3f}, {w_max:.3f}] electrical W/module "
            f"(coefficient range {coeff_min:.6f} .. {coeff_max:.6f}; achievable mean range: "
            f"{min_mean if min_mean is not None else 'n/a'} .. "
            f"{max_mean if max_mean is not None else 'n/a'})."
        )

    AtA = cast(FloatArray, A.T @ A)
    Atb = cast(FloatArray, A.T @ b)
    if lambda_smooth > 0.0:
        D = (
            build_D_groups(smooth_groups, K)
            if smooth_groups is not None
            else build_D(K)
        )
        AtA = AtA + (lambda_smooth * (D.T @ D))

    def fun(w: FloatArray) -> float:
        r = A @ w - b
        return 0.5 * float(r @ r)

    def jac(w: FloatArray) -> FloatArray:
        return cast(FloatArray, AtA @ w - Atb)

    def hess(_: FloatArray) -> FloatArray:
        return AtA

    bounds = Bounds(coeff_min, coeff_max)
    constraints = [LinearConstraint(mean_row, [target_mu], [target_mu])]

    res = minimize(
        fun,
        x0,
        method="trust-constr",
        jac=jac,
        hess=hess,
        bounds=bounds,
        constraints=constraints,
        options={"verbose": 0, "maxiter": 5000},
    )
    coeff_best = cast(FloatArray, res.x)
    mean_err = abs((mean_row @ coeff_best).item() - target_mu)
    if not res.success or mean_err > abs(target_mu) * tol_mean + 1e-6:
        # Fallback: SLSQP sometimes handles tight equalities more robustly.
        def mean_constraint(w: FloatArray) -> float:
            return float((mean_row @ w).item() - target_mu)

        res2 = minimize(
            fun,
            x0,
            method="SLSQP",
            jac=jac,
            bounds=bounds,
            constraints=[{"type": "eq", "fun": mean_constraint}],
            options={"ftol": 1e-12, "maxiter": 5000},
        )
        if res2.success:
            coeff_best = cast(FloatArray, res2.x)
            mean_err = abs((mean_row @ coeff_best).item() - target_mu)
        else:
            raise SystemExit(f"Min-variance QP did not converge: {res2.message}")

    if mean_err > abs(target_mu) * tol_mean + 1e-6:
        raise SystemExit(
            f"Min-variance QP mean constraint not met (|Δμ|={mean_err:.6f})."
        )

    p = cast(FloatArray, A @ coeff_best)
    setpoint = target_mu if _cap_metrics_enabled(runtime_env) else None
    m = metrics_from_field(p, setpoint_ppfd=setpoint, legacy_metrics=legacy_metrics)
    weights_best = transform.weights_from_coeffs(coeff_best, w_min=w_min, w_max=w_max)
    details = {
        "solve_space": transform.solve_space,
        "basis_unit_w_per_module": float(transform.basis_unit_w_per_module),
        "basis_source_photon_umol_s": float(transform.basis_source_photon_umol_s),
        "basis_coefficients": [float(x) for x in coeff_best],
    }
    return weights_best, m, details


def _load_solver_inputs(
    config: UniformitySolverConfig, env: Mapping[str, str], log: SolverLogger
) -> SolverInputs:
    basis_path = config.basis_path
    if not basis_path.exists():
        raise SystemExit(f"Basis file not found: {basis_path}")
    matrix = validate_basis_matrix(load_basis(basis_path))
    n_points, n_variables = matrix.shape
    log(f"Loaded A with shape (n_points={n_points}, n_vars={n_variables})")
    manifest = load_basis_manifest(basis_path, runtime_env=env)
    transform = build_basis_solve_transform(basis_manifest=manifest, runtime_env=env)
    return SolverInputs(
        basis_path=basis_path,
        matrix=matrix,
        manifest=manifest,
        transform=transform,
        n_points=int(n_points),
        n_variables=int(n_variables),
    )


def _extract_variable_metadata(
    manifest: JsonObject | None, n_variables: int
) -> SolverVariableMetadata:
    var_mode: str | None = None
    ring_group: int | None = None
    outer_indices: list[Any] = []
    outer_ring_index: Any = None
    ring_indices: Any = None
    module_indices: Any = None
    if isinstance(manifest, dict):
        raw_var_mode = manifest.get("variables")
        var_mode = raw_var_mode if isinstance(raw_var_mode, str) else None
        ring_indices = manifest.get("ring_indices")
        outer_indices = list(manifest.get("outer_ring_indices") or [])
        outer_ring_index = manifest.get("outer_ring_index")
        module_indices = manifest.get("module_indices")
        groups = manifest.get("variable_groups") or {}
        if isinstance(groups, Mapping):
            try:
                rings_value = groups.get("rings")
                ring_group = int(rings_value) if rings_value is not None else None
            except (TypeError, ValueError):
                ring_group = None
    if var_mode == "ring_plus_outer_modules" and ring_group is not None:
        if ring_group + len(outer_indices) != n_variables:
            var_mode = None
            ring_group = None
    return SolverVariableMetadata(
        var_mode=var_mode,
        ring_group=ring_group,
        outer_indices=outer_indices,
        outer_ring_index=outer_ring_index,
        ring_indices=ring_indices,
        module_indices=module_indices,
    )


def _log_solver_metadata(
    metadata: SolverVariableMetadata,
    transform: BasisSolveTransform,
    log: SolverLogger,
) -> None:
    if metadata.var_mode:
        log(f"Variable mode: {metadata.var_mode}")
    else:
        log("Variable mode: rings (default)")
    if transform.is_curve:
        log(
            "Solve space: curve photon coefficients "
            f"(basis {transform.basis_unit_w_per_module:.3f} W/module -> "
            f"{transform.basis_source_photon_umol_s:.6f} umol/s source)"
        )
    else:
        log("Solve space: electrical watts/module (legacy linear basis)")


def _ridge_weights(config: UniformitySolverConfig, n_variables: int) -> FloatArray:
    if config.ridge_weights is None or len(config.ridge_weights) == 0:
        return np.ones((n_variables,), dtype=float)
    if len(config.ridge_weights) == 1:
        return np.full((n_variables,), float(config.ridge_weights[0]), dtype=float)
    if len(config.ridge_weights) != n_variables:
        raise SystemExit(
            f"--ridge-weights length {len(config.ridge_weights)} must match K={n_variables}"
        )
    return np.asarray([float(x) for x in config.ridge_weights], dtype=float)


def _prepare_lambda_settings(
    config: UniformitySolverConfig,
    *,
    n_variables: int,
    per_module_mode: bool,
    log: SolverLogger,
) -> SolverLambdaSettings:
    lambdas_s = list(config.lambda_s)
    lambdas_r = list(config.lambda_r)
    lambda_smooth = config.lambda_smooth
    if per_module_mode:
        if any(weight > 0 for weight in lambdas_s) or lambda_smooth > 0:
            log("Note: smoothing disabled for per-module variables.")
        lambdas_s = [0.0]
        lambda_smooth = 0.0
    log(f"Solver method: {config.solve_method}")
    if config.solve_method == "lsq":
        log(f"Trying lambda_s values: {lambdas_s}")
        log(f"Trying lambda_r values: {lambdas_r}")
        log(f"Mean weight lambda_mean={config.lambda_mean}")
        if lambda_smooth > 0:
            log(
                f"Note: --lambda-smooth={lambda_smooth} ignored for lsq; use --lambda-s."
            )
    elif lambda_smooth > 0:
        log(f"Smoothness weight lambda_smooth={lambda_smooth}")

    smooth_groups: list[int] | None = None
    if config.smooth_groups is not None and len(config.smooth_groups) > 0:
        smooth_groups = [int(x) for x in config.smooth_groups]
    if per_module_mode:
        smooth_groups = None
    return SolverLambdaSettings(
        lambdas_s=lambdas_s,
        lambdas_r=lambdas_r,
        lambda_smooth=lambda_smooth,
        ridge_w=_ridge_weights(config, n_variables),
        smooth_groups=smooth_groups,
    )


def _solution_details(transform: BasisSolveTransform, coeffs: FloatArray) -> JsonObject:
    return {
        "solve_space": transform.solve_space,
        "basis_unit_w_per_module": float(transform.basis_unit_w_per_module),
        "basis_source_photon_umol_s": float(transform.basis_source_photon_umol_s),
        "basis_coefficients": [float(x) for x in coeffs],
    }


def _legacy_metric_dict(metrics: JsonObject) -> JsonObject:
    legacy = metrics.get("legacy") or {}
    return legacy if isinstance(legacy, dict) else {}


def _solution_score(
    metrics: JsonObject,
    weights: FloatArray,
    metadata: SolverVariableMetadata,
) -> SolverScore:
    if metadata.per_module_mode:
        smooth_pen = 0.0
    elif metadata.ring_group is not None and metadata.ring_group >= 2:
        diffs = np.diff(weights[: metadata.ring_group])
        smooth_pen = np.std(diffs) / max(1e-9, np.mean(weights))
    else:
        diffs = np.diff(weights)
        smooth_pen = np.std(diffs) / max(1e-9, np.mean(weights))
    legacy = _legacy_metric_dict(metrics)
    return (
        float(legacy.get("cv_percent", float("inf"))),
        float(smooth_pen),
        -float(legacy.get("min_over_avg", 0.0)),
    )


def _try_lsq_pair(
    *,
    A: FloatArray,
    target_mu: float,
    ones: FloatArray,
    mean_row: FloatArray,
    transform: BasisSolveTransform,
    config: UniformitySolverConfig,
    settings: SolverLambdaSettings,
    metadata: SolverVariableMetadata,
    env: Mapping[str, str],
    lam_s: float,
    lam_r: float,
    log: SolverLogger,
) -> tuple[BestSolution, JsonObject] | None:
    n_points, n_variables = A.shape
    log(f"\n--- lambda_s = {lam_s}, lambda_r = {lam_r} ---")
    aug_rows: list[FloatArray] = [A]
    aug_rhs: list[FloatArray] = [cast(FloatArray, target_mu * ones)]
    if lam_s > 0.0:
        D = (
            build_D_groups(settings.smooth_groups, n_variables)
            if settings.smooth_groups is not None
            else build_D(n_variables)
        )
        aug_rows.append(cast(FloatArray, np.sqrt(lam_s) * D))
        aug_rhs.append(np.zeros((D.shape[0],), dtype=float))
    if lam_r > 0.0:
        W = cast(FloatArray, np.diag(np.sqrt(settings.ridge_w)))
        aug_rows.append(cast(FloatArray, np.sqrt(lam_r) * W))
        aug_rhs.append(np.zeros((n_variables,), dtype=float))
    if config.lambda_mean > 0.0:
        aug_rows.append(cast(FloatArray, np.sqrt(config.lambda_mean) * mean_row))
        aug_rhs.append(np.array([np.sqrt(config.lambda_mean) * target_mu], dtype=float))

    coeff_min, coeff_max = transform.coeff_bounds(config.w_min, config.w_max)
    res = lsq_linear(
        cast(FloatArray, np.vstack(aug_rows)),
        cast(FloatArray, np.concatenate(aug_rhs)),
        bounds=(coeff_min, coeff_max),
        method="trf",
    )
    if not res.success:
        log(f"  lsq_linear did not converge: {res.message}", level="warning")
        return None

    coeff_raw = cast(FloatArray, res.x)
    p_raw = cast(FloatArray, A @ coeff_raw)
    m_raw = float(p_raw.mean())
    log(f"  raw mean={m_raw:.3f}, target={target_mu:.3f}")
    alpha = target_mu / m_raw if m_raw > 0 else 1.0
    coeff_scaled = cast(FloatArray, np.clip(alpha * coeff_raw, coeff_min, coeff_max))
    p_scaled = cast(FloatArray, A @ coeff_scaled)
    m_scaled = float(p_scaled.mean())
    log(f"  scaled mean={m_scaled:.3f} (alpha={alpha:.4f})")
    mean_err = abs(m_scaled - target_mu)
    if mean_err > abs(target_mu) * config.tol_mean + 1e-6:
        log(
            f"  WARNING: mean off target by {mean_err:.3f} (tol={config.tol_mean:.3f})",
            level="warning",
        )
    setpoint = target_mu if _cap_metrics_enabled(env) else None
    metrics = metrics_from_field(
        p_scaled, setpoint_ppfd=setpoint, legacy_metrics=config.legacy_metrics
    )
    log(" " + format_ppfd_metrics_line(metrics))
    weights_scaled = transform.weights_from_coeffs(
        coeff_scaled, w_min=config.w_min, w_max=config.w_max
    )
    score = _solution_score(metrics, weights_scaled, metadata)
    return (score, (lam_s, lam_r), weights_scaled, metrics), _solution_details(
        transform, coeff_scaled
    )


def _solve_lsq_best(
    *,
    A: FloatArray,
    target_mu: float,
    transform: BasisSolveTransform,
    config: UniformitySolverConfig,
    settings: SolverLambdaSettings,
    metadata: SolverVariableMetadata,
    env: Mapping[str, str],
    log: SolverLogger,
) -> tuple[BestSolution | None, JsonObject | None]:
    n_points, _n_variables = A.shape
    ones = np.ones((n_points,), dtype=float)
    mean_row = cast(FloatArray, A.mean(axis=0, keepdims=True))
    best: BestSolution | None = None
    best_details: JsonObject | None = None
    for lam_s in settings.lambdas_s:
        for lam_r in settings.lambdas_r:
            candidate = _try_lsq_pair(
                A=A,
                target_mu=target_mu,
                ones=ones,
                mean_row=mean_row,
                transform=transform,
                config=config,
                settings=settings,
                metadata=metadata,
                env=env,
                lam_s=lam_s,
                lam_r=lam_r,
                log=log,
            )
            if candidate is not None and (best is None or candidate[0][0] < best[0]):
                best, best_details = candidate
    if config.use_chebyshev:
        candidate = _try_chebyshev_solution(
            A=A,
            target_mu=target_mu,
            mean_row=mean_row,
            transform=transform,
            config=config,
            env=env,
            log=log,
        )
        if candidate is not None and (best is None or candidate[0][0] < best[0]):
            best, best_details = candidate
    return best, best_details


def _try_chebyshev_solution(
    *,
    A: FloatArray,
    target_mu: float,
    mean_row: FloatArray,
    transform: BasisSolveTransform,
    config: UniformitySolverConfig,
    env: Mapping[str, str],
    log: SolverLogger,
) -> tuple[BestSolution, JsonObject] | None:
    log("\n--- Chebyshev (minimax) solve ---")
    n_points, n_variables = A.shape
    c = np.zeros(n_variables + 1, dtype=float)
    c[-1] = 1.0
    A_ub = cast(
        FloatArray,
        np.vstack(
            [
                np.hstack([A, -np.ones((n_points, 1), dtype=float)]),
                np.hstack([-A, -np.ones((n_points, 1), dtype=float)]),
            ]
        ),
    )
    b_ub = cast(
        FloatArray,
        np.concatenate(
            [
                target_mu * np.ones((n_points,), dtype=float),
                -target_mu * np.ones((n_points,), dtype=float),
            ]
        ),
    )
    A_eq = cast(FloatArray, np.hstack([mean_row, np.zeros((1, 1), dtype=float)]))
    coeff_min, coeff_max = transform.coeff_bounds(config.w_min, config.w_max)
    res_lp = linprog(
        c,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=A_eq,
        b_eq=np.array([target_mu], dtype=float),
        bounds=[(coeff_min, coeff_max)] * n_variables + [(0.0, None)],
        method="highs",
    )
    if not res_lp.success:
        log(f"  Chebyshev solve failed: {res_lp.message}", level="warning")
        return None
    coeff_lp = cast(FloatArray, res_lp.x[:n_variables])
    p_lp = cast(FloatArray, A @ coeff_lp)
    setpoint = target_mu if _cap_metrics_enabled(env) else None
    metrics = metrics_from_field(
        p_lp, setpoint_ppfd=setpoint, legacy_metrics=config.legacy_metrics
    )
    log(f"  minimax t={res_lp.fun:.3f}  " + format_ppfd_metrics_line(metrics))
    legacy = _legacy_metric_dict(metrics)
    score = (
        float(legacy.get("cv_percent", float("inf"))),
        -float(legacy.get("min_over_avg", 0.0)),
    )
    return (
        score,
        ("chebyshev", None),
        transform.weights_from_coeffs(coeff_lp, w_min=config.w_min, w_max=config.w_max),
        metrics,
    ), _solution_details(transform, coeff_lp)


def _solve_best_solution(
    inputs: SolverInputs,
    config: UniformitySolverConfig,
    settings: SolverLambdaSettings,
    metadata: SolverVariableMetadata,
    env: Mapping[str, str],
    log: SolverLogger,
) -> tuple[BestSolution, JsonObject | None]:
    if config.solve_method == "minvar_qp":
        weights, metrics, details = solve_minvar_qp(
            inputs.matrix,
            config.target_ppfd,
            config.w_min,
            config.w_max,
            lambda_smooth=settings.lambda_smooth,
            smooth_groups=settings.smooth_groups,
            legacy_metrics=config.legacy_metrics,
            tol_mean=config.tol_mean,
            basis_manifest=inputs.manifest,
            runtime_env=env,
        )
        return ((0.0, 0.0, 0.0), ("minvar_qp", None), weights, metrics), details
    best, lsq_details = _solve_lsq_best(
        A=inputs.matrix,
        target_mu=config.target_ppfd,
        transform=inputs.transform,
        config=config,
        settings=settings,
        metadata=metadata,
        env=env,
        log=log,
    )
    if best is None:
        raise SystemExit("No successful solution found.")
    return best, lsq_details


def _ensure_best_details(
    details: JsonObject | None, transform: BasisSolveTransform, weights: FloatArray
) -> JsonObject:
    if details is not None:
        return details
    return _solution_details(transform, transform.coeffs_from_weights(weights))


def _log_best_solution(
    *,
    choice: SolverChoice,
    weights: FloatArray,
    metrics: JsonObject,
    metadata: SolverVariableMetadata,
    n_variables: int,
    lambda_mean: float,
    log: SolverLogger,
) -> None:
    log("\n=== Best solution ===")
    if choice[0] == "chebyshev":
        log("strategy=chebyshev (minimax)")
    elif choice[0] == "minvar_qp":
        log("strategy=minvar_qp (exact min-variance)")
    else:
        log(f"lambda_s={choice[0]}, lambda_r={choice[1]}, lambda_mean={lambda_mean}")
    if metadata.per_module_mode:
        w_min = float(np.min(weights))
        w_max = float(np.max(weights))
        w_avg = float(np.mean(weights))
        log(
            f"per-module powers: {n_variables} variables "
            f"(min={w_min:.3f}, avg={w_avg:.3f}, max={w_max:.3f})"
        )
    elif (
        metadata.var_mode == "ring_plus_outer_modules"
        and metadata.ring_group is not None
    ):
        log("ring powers (W per module):")
        use_indices = (
            metadata.ring_indices
            if isinstance(metadata.ring_indices, list)
            and len(metadata.ring_indices) == metadata.ring_group
            else list(range(metadata.ring_group))
        )
        for i, wi in zip(use_indices, weights[: metadata.ring_group]):
            log(f"  ring {int(i)}: {wi:.3f} W")
        log(f"outer ring modules: {len(weights[metadata.ring_group :])} variables")
    else:
        log("ring powers (W per module):")
        for i, wi in enumerate(weights):
            log(f"  ring {i}: {wi:.3f} W")
    log(format_ppfd_metrics_line(metrics))


def _strategy_name(choice: SolverChoice) -> str:
    if choice[0] == "chebyshev":
        return "chebyshev"
    if choice[0] == "minvar_qp":
        return "minvar_qp"
    return "lsq"


def _layout_metadata(manifest: JsonObject | None, env: Mapping[str, str]) -> JsonObject:
    source = manifest or {}
    emitter_env = source.get("emitter_env", {})
    if not isinstance(emitter_env, Mapping):
        emitter_env = {}
    n_rings = source.get("n_rings")
    return {
        "layout_mode": emitter_env.get("LAYOUT_MODE", env.get("LAYOUT_MODE", "square")),
        "layout_family": source.get("layout_family"),
        "room_L_m": source.get("room_L_m"),
        "room_W_m": source.get("room_W_m"),
        "ring_n": (int(n_rings) - 1) if n_rings is not None else None,
        "rings": n_rings,
        "base_n": source.get("base_n"),
    }


def _add_variable_payload(
    out: JsonObject,
    *,
    metadata: SolverVariableMetadata,
    weights: FloatArray,
    n_variables: int,
) -> None:
    if metadata.per_module_mode:
        _add_per_module_payload(out, metadata, weights, n_variables)
        return
    if (
        metadata.var_mode == "ring_plus_outer_modules"
        and metadata.ring_group is not None
    ):
        _add_ring_plus_outer_payload(out, metadata, weights)
        return
    _add_ring_payload(out, weights, n_variables)


def _add_per_module_payload(
    out: JsonObject,
    metadata: SolverVariableMetadata,
    weights: FloatArray,
    n_variables: int,
) -> None:
    use_indices = (
        metadata.module_indices
        if isinstance(metadata.module_indices, list)
        and len(metadata.module_indices) == n_variables
        else list(range(n_variables))
    )
    out.update(
        {
            "variables": "per_module",
            "module_indices": [int(x) for x in use_indices],
            "module_powers_W_per_module": [float(x) for x in weights],
            "variable_groups": {"modules": int(n_variables)},
        }
    )


def _add_ring_plus_outer_payload(
    out: JsonObject, metadata: SolverVariableMetadata, weights: FloatArray
) -> None:
    if metadata.ring_group is None:
        return
    use_indices = (
        metadata.ring_indices
        if isinstance(metadata.ring_indices, list)
        and len(metadata.ring_indices) == metadata.ring_group
        else list(range(metadata.ring_group))
    )
    out.update(
        {
            "variables": "ring_plus_outer_modules",
            "ring_indices": [int(x) for x in use_indices],
            "ring_powers_W_per_module": [
                float(x) for x in weights[: metadata.ring_group]
            ],
            "outer_ring_index": int(metadata.outer_ring_index)
            if metadata.outer_ring_index is not None
            else None,
            "outer_ring_indices": [int(x) for x in metadata.outer_indices],
            "outer_ring_powers_W_per_module": [
                float(x) for x in weights[metadata.ring_group :]
            ],
            "variable_groups": {
                "rings": int(metadata.ring_group),
                "outer_modules": int(len(weights) - metadata.ring_group),
            },
        }
    )


def _add_ring_payload(out: JsonObject, weights: FloatArray, n_variables: int) -> None:
    out.update(
        {
            "variables": "rings",
            "ring_indices": list(range(n_variables)),
            "ring_powers_W_per_module": [float(x) for x in weights],
        }
    )


def _build_solution_output(
    *,
    config: UniformitySolverConfig,
    inputs: SolverInputs,
    metadata: SolverVariableMetadata,
    choice: SolverChoice,
    weights: FloatArray,
    metrics: JsonObject,
    details: JsonObject,
    env: Mapping[str, str],
) -> JsonObject:
    out: JsonObject = {
        "target_ppfd": config.target_ppfd,
        "strategy": _strategy_name(choice),
        "lambda_s_best": None if choice[0] in ("chebyshev", "minvar_qp") else choice[0],
        "lambda_r_best": None if choice[0] in ("chebyshev", "minvar_qp") else choice[1],
        "lambda_mean": None if choice[0] == "minvar_qp" else config.lambda_mean,
        "lambda_smooth": float(config.lambda_smooth)
        if choice[0] == "minvar_qp"
        else None,
        "metrics": metrics,
        "basis_file": str(inputs.basis_path),
        "n_points": int(inputs.n_points),
        "solve_space": details["solve_space"],
        "basis_unit_w_per_module": details["basis_unit_w_per_module"],
        "basis_source_photon_umol_s": details["basis_source_photon_umol_s"],
        "basis_coefficients": details["basis_coefficients"],
    }
    out["smd_solution_metadata"] = build_smd_solution_metadata(
        runtime_fingerprint=build_smd_runtime_fingerprint_from_env(
            layout_meta=_layout_metadata(inputs.manifest, env),
            module_count=(inputs.manifest or {}).get("layout_modules"),
        ),
        basis_manifest=inputs.manifest,
        basis_path=inputs.basis_path,
        n_points=inputs.n_points,
    )
    _add_variable_payload(
        out, metadata=metadata, weights=weights, n_variables=inputs.n_variables
    )
    return out


def run_uniformity_solver(
    config: UniformitySolverConfig,
    *,
    runtime_env: Mapping[str, Any] | None = None,
    emit: Callable[[SolverEvent], None] | None = None,
) -> UniformitySolverResult:
    env = _stringify_env(runtime_env) or _stringify_env(os.environ)
    events: list[SolverEvent] = []

    def log(message: str, *, level: str = "info") -> None:
        event = SolverEvent(level=level, message=message)
        events.append(event)
        if emit is not None:
            emit(event)

    inputs = _load_solver_inputs(config, env, log)
    metadata = _extract_variable_metadata(inputs.manifest, inputs.n_variables)
    _log_solver_metadata(metadata, inputs.transform, log)
    settings = _prepare_lambda_settings(
        config,
        n_variables=inputs.n_variables,
        per_module_mode=metadata.per_module_mode,
        log=log,
    )
    best, best_details = _solve_best_solution(
        inputs, config, settings, metadata, env, log
    )
    _score, choice, weights, metrics = best
    details = _ensure_best_details(best_details, inputs.transform, weights)
    _log_best_solution(
        choice=choice,
        weights=weights,
        metrics=metrics,
        metadata=metadata,
        n_variables=inputs.n_variables,
        lambda_mean=config.lambda_mean,
        log=log,
    )
    out = _build_solution_output(
        config=config,
        inputs=inputs,
        metadata=metadata,
        choice=choice,
        weights=weights,
        metrics=metrics,
        details=details,
        env=env,
    )
    config.out_json.write_text(json.dumps(out, indent=2))
    log(f"\nSaved {config.out_json}")
    return UniformitySolverResult(
        output_path=config.out_json,
        output_payload=out,
        events=tuple(events),
        n_points=inputs.n_points,
        n_variables=inputs.n_variables,
    )


def main() -> None:
    config = UniformitySolverConfig.from_args(parse_args())
    run_uniformity_solver(
        config,
        runtime_env=os.environ,
        emit=lambda event: print(event.message),
    )


if __name__ == "__main__":
    main()
