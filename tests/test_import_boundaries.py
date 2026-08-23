from __future__ import annotations

import ast
from pathlib import Path


def test_source_imports_are_standard_library_or_package_local() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    allowed_roots = {
        "__future__",
        "argparse",
        "asyncio",
        "bisect",
        "collections",
        "concurrent",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "email",
        "enum",
            "fcntl",
            "fspm_optics",
            "functools",
            "html",
        "json",
        "hashlib",
        "io",
        "importlib",
        "itertools",
        "logging",
        "math",
        "mimetypes",
        "numpy",
        "os",
        "pathlib",
        "platform",
        "random",
        "re",
        "statistics",
        "starlette",
        "stat",
        "struct",
        "subprocess",
        "sys",
        "tempfile",
        "scipy",
        "shutil",
        "time",
        "threading",
        "types",
        "typing",
        "uuid",
        "uvicorn",
        "zipfile",
        "zlib",
    }
    for source_path in package_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert roots <= allowed_roots, source_path
            elif isinstance(node, ast.ImportFrom) and node.level:
                continue
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                assert root in allowed_roots, source_path


def test_conventional_phase22a_has_no_legacy_or_execution_import_boundary() -> None:
    package_root = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "fixtures"
        / "conventional_led"
    )
    forbidden_text = (
        ".salvage_source",
        "/home/austin/Desktop/horticulture-lighting-simulator",
        "/home/austin/Desktop/ies_sources",
        "subprocess",
        "ies2rad",
        "rcontrib",
        "rfluxmtx",
        "oconv",
        "rtrace",
    )
    phase22a_modules = (
        "angular.py",
        "errors.py",
        "lm63.py",
        "profile.py",
        "resources.py",
        "spectral.py",
    )
    for module_name in phase22a_modules:
        source_path = package_root / module_name
        text = source_path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_text), source_path


def test_phase25a_shared_photometry_and_hps_core_have_clean_import_boundaries() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    checked = (
        package_root / "photometry" / "lm63.py",
        package_root / "photometry" / "type_c.py",
        package_root / "fixtures" / "hps" / "angular.py",
        package_root / "fixtures" / "hps" / "lm63.py",
        package_root / "fixtures" / "hps" / "profile.py",
        package_root / "fixtures" / "hps" / "radiance_source.py",
        package_root / "fixtures" / "hps" / "resources.py",
    )
    forbidden_text = (
        ".salvage_source",
        "/home/austin/Desktop",
        "subprocess",
        "LocalRunner",
        "fixtures.conventional_led",
        "fixtures.smd",
        "sources.smd",
    )
    for source_path in checked:
        text = source_path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_text), source_path


def test_phase25b_hps_science_has_no_execution_or_cross_source_imports() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    checked = (
        package_root / "fixtures" / "hps" / "layout.py",
        package_root / "fixtures" / "hps" / "spectral.py",
        package_root / "fixtures" / "hps" / "band_source.py",
        package_root / "optics" / "hps.py",
        package_root / "transport" / "hps.py",
    )
    forbidden_text = (
        ".salvage_source",
        "/home/austin/Desktop",
        "subprocess",
        "LocalRunner",
        "conventional_rex_execution",
        "five_band_execution",
        "fixtures.conventional_led",
        "fixtures.smd",
        "sources.smd",
    )
    for source_path in checked:
        text = source_path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_text), source_path


def test_phase25c_hps_native_adapter_has_no_direct_or_cross_source_execution() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    checked = (
        package_root / "radiance" / "native_ies.py",
        package_root / "transport" / "native_scalar_data.py",
        package_root / "transport" / "hps_scalar.py",
    )
    forbidden_text = (
        ".salvage_source",
        "/home/austin/Desktop",
        "import subprocess",
        "shell=True",
        "fixtures.conventional_led",
        "fixtures.smd",
        "sources.smd",
        "conventional_scalar",
        "conventional_rex_execution",
        "five_band_execution",
    )
    for source_path in checked:
        text = source_path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_text), source_path


def test_phase25d_hps_rex_adapter_has_no_direct_or_cross_source_execution() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    checked = (
        package_root / "transport" / "native_rex_data.py",
        package_root / "transport" / "hps_rex_execution.py",
    )
    forbidden_text = (
        ".salvage_source",
        "/home/austin/Desktop",
        "import subprocess",
        "shell=True",
        "fixtures.conventional_led",
        "fixtures.smd",
        "sources.smd",
        "conventional_rex_execution",
    )
    for source_path in checked:
        text = source_path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_text), source_path


def test_phase27a_juvenile_radiance_export_is_source_and_runner_neutral() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "plants"
        / "radiance_scene_export.py"
    )
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        module == forbidden or f".{forbidden}" in module
        for module in imported_modules
        for forbidden in (
            "fixtures",
            "sources",
            "spectral",
            "optics",
            "transport",
            "viewer",
            "radiance.runner",
            "radiance.commands",
            "subprocess",
        )
    )
    assert not any(
        token in text
        for token in (
            ".salvage_source",
            "/home/austin/Desktop",
            "target_ppfd",
            "dimming",
            "rtrace",
            "oconv",
            "octree",
            "LocalRunner",
            "browser",
            "mature",
            "1024",
            "5248",
        )
    )


def test_phase27g_c_aggregation_has_no_target_display_or_source_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "fspm_science.py"
    )
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "application.target_control",
        "application.visualization",
        "fixtures",
        "sources",
        "viewer",
        "radiance.commands",
        "radiance.runner",
        "subprocess",
    )

    assert not any(
        module == value or module.endswith(f".{value}")
        for module in imported_modules
        for value in forbidden
    )
    assert ".salvage_source" not in text
    assert "target_ppfd" not in text


def test_phase27d_visualization_has_no_execution_or_physics_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "visualization.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "radiance.commands",
        "radiance.runner",
        "application.target_control",
        "optimization",
        "scipy",
        "subprocess",
    )

    assert not any(
        module == value or module.endswith(f".{value}")
        for module in imported_modules
        for value in forbidden
    )


def test_phase27g_d1_calibration_has_no_modeled_source_or_publication_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "surface_flux_calibration.py"
    )
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "fixtures",
        "sources",
        "application.domain",
        "application.systems",
        "application.target_control",
        "application.publication",
        "application.visualization",
        "application.proposed",
        "viewer",
    )

    assert not any(
        module == value or value in module
        for module in imported_modules
        for value in forbidden
    )
    assert not any(
        token in text
        for token in (
            "system_id",
            "target_control",
            "mount_height",
            ".IES",
            ".ies",
            ".DAT",
            ".dat",
        )
    )


def test_phase27g_d2_display_has_no_execution_or_system_specific_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "surface_flux_display.py"
    )
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "application.target_control",
        "application.systems",
        "multispectral",
        "fixtures",
        "sources",
        "radiance.commands",
        "radiance.runner",
        "subprocess",
    )

    assert "importlib" in imported_modules
    assert not any(
        module == value or value in module
        for module in imported_modules
        for value in forbidden
    )
    assert not any(
        token in text
        for token in (
            ".salvage_source",
            "/home/austin/Desktop",
            "/Downloads/",
            ".surface-flux-calibration/",
            "surface-flux-front-calibration-inputs",
            ".tar.gz",
            "held_out_acceptance_evidence",
            "system_id",
            "Proposed",
            "Conventional",
            "hps",
        )
    )
    assert 'resources.files("fspm_optics")' in text
    assert '"resources",\n                "calibration"' in text


def test_phase27g_d5b1_resource_has_no_display_publication_or_execution_imports() -> None:
    package_root = (
        Path(__file__).parents[1] / "src" / "fspm_optics"
    )
    checked = (
        package_root
        / "application"
        / "surface_flux_optimized_calibration.py",
        package_root
        / "application"
        / "surface_flux_optimized_calibration_promotion.py",
        package_root / "surface_flux_optimized_calibration_promotion_cli.py",
    )
    forbidden = (
        "application.publication",
        "application.surface_flux_display",
        "application.visualization",
        "radiance.commands",
        "radiance.runner",
        "viewer",
        "subprocess",
    )
    for source_path in checked:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            module == value or module.endswith(f".{value}")
            for module in imported_modules
            for value in forbidden
        ), source_path


def test_phase27h_e_baseline_leaf_science_has_no_stage_b_or_surface_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "baseline_leaf_uniformity.py"
    )
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = (
        "receivers",
        "absorption",
        "application.fspm_science",
        "application.multispectral",
        "application.surface_flux_display",
        "application.surface_flux_calibration",
    )

    assert not any(
        module == value or f".{value}" in module
        for module in imported_modules
        for value in forbidden
    )
    assert "iter_receivers" not in text
    assert "iter_patches" not in text
