from __future__ import annotations

import os
from typing import Any, Mapping

from rad_rebuild.radiance.domain import (
    canonicalize_quality_preset as _domain_quality_preset,
)


BASIS_BACKEND_RTRACE = "rtrace"
BASIS_BACKEND_RCONTRIB_LEGACY = "rcontrib_legacy"
BASIS_BACKEND_RCONTRIB_MCPT = "rcontrib_mcpt"
DEFAULT_SMD_BASIS_BACKEND = BASIS_BACKEND_RTRACE
SUPPORTED_SMD_BASIS_BACKENDS = (
    BASIS_BACKEND_RTRACE,
    BASIS_BACKEND_RCONTRIB_LEGACY,
    BASIS_BACKEND_RCONTRIB_MCPT,
)


_RTRACE_MODE_PRESETS: dict[str, dict[str, float | int | None]] = {
    "direct": {
        "ab": 0,
        "aa": 0.0,
        "ad": None,
        "as": None,
        "ar": None,
        "dc": 1.0,
        "dj": 0.60,
        "dr": 0,
        "ds": 0.08,
        "dt": 0.0,
        "lr": 0,
        "lw": 1e-5,
    },
    "standard": {
        "ab": 3,
        "aa": 0.22,
        "ad": 512,
        "as": 128,
        "ar": 48,
        "dc": 0.50,
        "dj": 0.35,
        "dr": 1,
        "ds": 0.40,
        "dt": 0.08,
        "lr": 6,
        "lw": 2e-4,
    },
    "quality": {
        "ab": 5,
        "aa": 0.12,
        "ad": 2048,
        "as": 512,
        "ar": 96,
        "dc": 0.85,
        "dj": 0.65,
        "dr": 3,
        "ds": 0.20,
        "dt": 0.03,
        "lr": 12,
        "lw": 5e-5,
    },
    "rigorous": {
        "ab": 6,
        "aa": 0.08,
        "ad": 4096,
        "as": 1024,
        "ar": 128,
        "dc": 0.90,
        "dj": 0.70,
        "dr": 4,
        "ds": 0.15,
        "dt": 0.02,
        "lr": 16,
        "lw": 2e-5,
    },
}


def _stringify_env(env: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(env, Mapping):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in env.items()}


def _env_value(env: Mapping[str, Any] | None, name: str) -> str:
    source = _stringify_env(env) or _stringify_env(os.environ)
    return str(source.get(name, "")).strip()


def _env_int(env: Mapping[str, Any] | None, name: str, default: int) -> int:
    raw = _env_value(env, name)
    if not raw:
        return int(default)
    try:
        return int(float(raw))
    except Exception:
        return int(default)


def _env_float(env: Mapping[str, Any] | None, name: str, default: float) -> float:
    raw = _env_value(env, name)
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def canonicalize_quality_preset(raw: str | None) -> str:
    return _domain_quality_preset(raw).value


def canonicalize_basis_backend(raw: str | None) -> str:
    token = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"", "default", BASIS_BACKEND_RTRACE}:
        return BASIS_BACKEND_RTRACE
    if token in {"legacy", BASIS_BACKEND_RCONTRIB_LEGACY}:
        return BASIS_BACKEND_RCONTRIB_LEGACY
    if token in {"mcpt", BASIS_BACKEND_RCONTRIB_MCPT}:
        return BASIS_BACKEND_RCONTRIB_MCPT
    raise ValueError(
        "Unsupported SMD basis backend "
        f"{raw!r}. Expected one of: {', '.join(SUPPORTED_SMD_BASIS_BACKENDS)}."
    )


def basis_backend_uses_rcontrib(raw: str | None) -> bool:
    return canonicalize_basis_backend(raw) in {
        BASIS_BACKEND_RCONTRIB_LEGACY,
        BASIS_BACKEND_RCONTRIB_MCPT,
    }


def validate_basis_backend_request(
    mode: str | None,
    basis_backend: str | None,
    *,
    variable_mode: str = "rings",
) -> str:
    backend = canonicalize_basis_backend(basis_backend)
    if backend == BASIS_BACKEND_RTRACE:
        return backend
    mode_token = (mode or "").strip().lower()
    if mode_token != "smd":
        raise ValueError(
            f"Basis backend {backend!r} is only supported for Our LED System / SMD mode."
        )
    variable_token = (variable_mode or "rings").strip().lower()
    if variable_token not in {"ring", "rings"}:
        raise ValueError(
            f"Basis backend {backend!r} is only supported for ring-wise SMD basis mode; "
            f"got variable mode {variable_mode!r}."
        )
    return backend


def basis_ring_modifier_name(index: int) -> str:
    return f"smd_basis_ring_{int(index):03d}"


def rtrace_mode_preset(sim_mode: str | None) -> dict[str, float | int | None]:
    return dict(_RTRACE_MODE_PRESETS[canonicalize_quality_preset(sim_mode)])


def _legacy_rcontrib_backend_config(
    backend: str, sim_mode: str | None
) -> dict[str, Any]:
    preset = rtrace_mode_preset(sim_mode)
    return {
        "basis_backend": backend,
        "preset_source": "current_rtrace_mode",
        "sim_mode": canonicalize_quality_preset(sim_mode),
        "ab": int(preset["ab"] or 0),
        "aa": float(preset["aa"] or 0.0),
        "ad": None if preset["ad"] is None else int(preset["ad"]),
        "as": None if preset["as"] is None else int(preset["as"]),
        "ar": None if preset["ar"] is None else int(preset["ar"]),
        "dc": float(preset["dc"] or 0.0),
        "dj": float(preset["dj"] or 0.0),
        "dr": int(preset["dr"] or 0),
        "ds": float(preset["ds"] or 0.0),
        "dt": float(preset["dt"] or 0.0),
        "lr": int(preset["lr"] or 0),
        "lw": float(preset["lw"] or 0.0),
    }


def _mcpt_backend_config(
    backend: str, sim_mode: str | None, env: Mapping[str, Any] | None
) -> dict[str, Any]:
    mode_preset = rtrace_mode_preset(sim_mode)
    default_ab = int(mode_preset["ab"] or 0)
    ad = _env_int(env, "SMD_RCONTRIB_MCPT_AD", 2048)
    ab = _env_int(env, "SMD_RCONTRIB_MCPT_AB", default_ab)
    lr_raw = _env_value(env, "SMD_RCONTRIB_MCPT_LR")
    lw_raw = _env_value(env, "SMD_RCONTRIB_MCPT_LW")
    lr = int(float(lr_raw)) if lr_raw else -(ab + 3)
    lw = float(lw_raw) if lw_raw else (1.0 / max(1.0, 2.0 * float(ad)))
    return {
        "basis_backend": backend,
        "preset_source": "mcpt_guidance_v1",
        "sim_mode": canonicalize_quality_preset(sim_mode),
        "ab": int(ab),
        "ad": int(ad),
        "lr": int(lr),
        "lw": float(lw),
        "u": True,
    }


def describe_basis_backend_config(
    basis_backend: str | None,
    *,
    sim_mode: str | None,
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    backend = canonicalize_basis_backend(basis_backend)
    if backend == BASIS_BACKEND_RTRACE:
        return {"basis_backend": backend}
    if backend == BASIS_BACKEND_RCONTRIB_LEGACY:
        return _legacy_rcontrib_backend_config(backend, sim_mode)
    return _mcpt_backend_config(backend, sim_mode, env)


def rcontrib_command_args(
    basis_backend: str | None,
    *,
    sim_mode: str | None,
    env: Mapping[str, Any] | None = None,
) -> list[str]:
    backend = canonicalize_basis_backend(basis_backend)
    if backend == BASIS_BACKEND_RTRACE:
        raise ValueError("rtrace does not use rcontrib command arguments")
    config = describe_basis_backend_config(backend, sim_mode=sim_mode, env=env)
    if backend == BASIS_BACKEND_RCONTRIB_LEGACY:
        args: list[str] = ["-ab", str(config["ab"])]
        for flag in ("ad", "as", "ar"):
            value = config[flag]
            if value is not None:
                args.extend([f"-{flag}", str(int(value))])
        args.extend(
            [
                "-aa",
                f"{float(config['aa']):g}",
                "-dc",
                f"{float(config['dc']):g}",
                "-dj",
                f"{float(config['dj']):g}",
                "-ds",
                f"{float(config['ds']):g}",
                "-dt",
                f"{float(config['dt']):g}",
                "-dr",
                str(int(config["dr"])),
                "-lr",
                str(int(config["lr"])),
                "-lw",
                f"{float(config['lw']):g}",
            ]
        )
        return args
    return [
        "-u+",
        "-ab",
        str(int(config["ab"])),
        "-ad",
        str(int(config["ad"])),
        "-lr",
        str(int(config["lr"])),
        "-lw",
        f"{float(config['lw']):g}",
    ]


def basis_backend_request_fields(
    basis_backend: str | None,
    *,
    sim_mode: str | None,
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    backend = canonicalize_basis_backend(basis_backend)
    payload: dict[str, Any] = {"basis_backend": backend}
    if backend != BASIS_BACKEND_RTRACE:
        payload["basis_backend_config"] = describe_basis_backend_config(
            backend,
            sim_mode=sim_mode,
            env=env,
        )
    return payload
