from __future__ import annotations

import importlib
import unittest
import warnings
from collections import Counter

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from scripts.dev.finding_parsers import finding_fingerprint  # noqa: E402
from scripts.dev import export_openapi  # noqa: E402
from scripts.dev.fingerprint_gates import compare_fingerprints, source_classification  # noqa: E402


class QualityGateContractTests(unittest.TestCase):
    def test_fingerprint_gate_fails_new_finding_even_when_count_is_unchanged(
        self,
    ) -> None:
        allowed = {"ruff-old-a", "ruff-old-b"}
        current = {"ruff-old-b", "ruff-new-c"}

        comparison = compare_fingerprints(current, allowed)

        self.assertEqual(comparison["new"], ["ruff-new-c"])
        self.assertEqual(comparison["resolved"], ["ruff-old-a"])

    def test_mypy_fingerprint_is_stable_when_only_line_moves(self) -> None:
        before = finding_fingerprint(
            "mypy",
            "arg-type",
            "src/example.py",
            10,
            None,
            "build",
            'Argument 1 to "build" has incompatible type "object"',
        )
        after = finding_fingerprint(
            "mypy",
            "arg-type",
            "src/example.py",
            25,
            4,
            "build",
            'Argument 1 to "build" has incompatible type "object"',
        )
        changed_symbol = finding_fingerprint(
            "mypy",
            "arg-type",
            "src/example.py",
            25,
            4,
            "other",
            'Argument 1 to "build" has incompatible type "object"',
        )

        self.assertEqual(before, after)
        self.assertNotEqual(before, changed_symbol)

    def test_fastapi_openapi_operation_ids_are_complete_unique_and_warning_free(
        self,
    ) -> None:
        server = importlib.import_module("rad_rebuild.radiance.backend.server")
        app = server.create_app()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            schema = app.openapi()

        first_party_warnings = [
            warning
            for warning in captured
            if source_classification(str(warning.filename)) == "first-party"
        ]
        self.assertEqual(first_party_warnings, [])

        operations: list[tuple[str, str, str]] = []
        missing_operation_ids: list[str] = []
        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                operation_id = operation.get("operationId")
                if not operation_id:
                    missing_operation_ids.append(f"{method.upper()} {path}")
                    continue
                operations.append((method, path, operation_id))

        duplicates = sorted(
            operation_id
            for operation_id, count in Counter(
                operation_id for _method, _path, operation_id in operations
            ).items()
            if count > 1
        )
        self.assertEqual(missing_operation_ids, [])
        self.assertEqual(duplicates, [])

    def test_public_json_routes_do_not_use_broad_dict_response_models(self) -> None:
        server = importlib.import_module("rad_rebuild.radiance.backend.server")
        schema = server.create_app().openapi()
        modeled_routes = {
            ("post", "/layout"): "LayoutResponse",
            ("post", "/radiance/run"): "RadianceRunResponse",
            ("get", "/jobs/{job_id}/tail"): "JobTailResponse",
            ("post", "/radiance/manifest"): "RadianceManifestResponse",
            ("get", "/radiance/images"): "RadianceImagesResponse",
            ("get", "/radiance/metrics"): "RadianceMetricsResponse",
            ("get", "/radiance/assembly-scene"): "AssemblySceneResponse",
            ("get", "/radiance/assembly-photometric-layer"): "AssemblyPhotometricLayerResponse",
            ("post", "/radiance/electrical-estimate"): "ElectricalEstimateResponse",
            ("get", "/docker/status"): "DockerStatusResponse",
            ("get", "/radiance/runtime/status"): "RuntimeStatusResponse",
            ("post", "/reproduce"): "ReproduceResponse",
        }
        found: dict[tuple[str, str], str] = {}
        broad: list[str] = []
        for key, expected_schema in modeled_routes.items():
            method, path = key
            response_schema = schema["paths"][path][method]["responses"]["200"][
                "content"
            ]["application/json"]["schema"]
            ref = response_schema.get("$ref", "")
            if not ref:
                broad.append(f"{method.upper()} {path}")
                continue
            found[key] = ref.rsplit("/", 1)[-1]

        self.assertEqual(broad, [])
        self.assertEqual(found, modeled_routes)

    def test_openapi_export_is_deterministic_and_documents_public_errors(self) -> None:
        self.assertEqual(export_openapi.main(["--check"]), 0)
        schema = importlib.import_module(
            "rad_rebuild.radiance.backend.server"
        ).create_app().openapi()
        self.assertIn("PublicErrorResponse", schema["components"]["schemas"])

        error_refs: list[str] = []
        for path_item in schema["paths"].values():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                for status_code, response in operation.get("responses", {}).items():
                    if status_code.startswith("2"):
                        continue
                    content = response.get("content", {})
                    json_schema = content.get("application/json", {}).get("schema", {})
                    ref = json_schema.get("$ref")
                    if ref:
                        error_refs.append(ref)

        self.assertIn("#/components/schemas/PublicErrorResponse", error_refs)


if __name__ == "__main__":
    unittest.main()
