from __future__ import annotations

import asyncio
from dataclasses import asdict
from functools import cache
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
from typing import Mapping
import zipfile

import httpx
import pytest

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    SUPPORTED_SYSTEM_IDS,
    SYSTEM_DISPLAY_NAMES,
    ConventionalRunRequest,
    ProposedRunRequest,
    ProposedSourceMode,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.fixtures.conventional_led.lm63 import load_approved_lm63
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_SPD_RESOURCE_NAME,
    conventional_resource_bytes,
)
from fspm_optics.fixtures.hps import (
    HPS_IES_RESOURCE_NAME,
    HPS_SPD_RESOURCE_NAME,
    hps_resource_bytes,
    load_hps_lm63,
)
from fspm_optics.fixtures.proposed_cob.source import COB_SOURCE_MODE
from fspm_optics.precomputed.committed_playback import (
    HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_CASE_DIGEST_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_PLAN_IDENTITY_SHA256,
    build_committed_playback_plan,
    inspect_committed_playback_catalog,
    load_committed_case_bundle,
)
from fspm_optics.viewer.fixtures import ASSET_REGISTRY
from fspm_optics.web.app import create_app
from fspm_optics.web.public import STATIC_ASSETS, create_public_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "src/fspm_optics"
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")
_EXPECTED_PRECOMPUTED_INVENTORY_SHA256 = (
    "ae8f85d39dcbb47e51552aa7d8d40b94b2900c98c09e3fa3b201cf05c6917699"
)

# These fingerprints lock the deny-list without recording the prohibited values.
# The values themselves are recovered only from immutable archives or assembled
# from deliberately separated fragments, matching the established anonymity gate.
_EXPECTED_DENY_FINGERPRINTS = frozenset(
    {
        "1d75503a3429229f734a0e564b83660cb815359fc1b976b6d73b687fdbfbccca",
        "2249a77e949801e8d01ec4a8353908b443d67d909e0822c9bcf7428898183fce",
        "2708f620f7984bc77deb65c8bfb3ab558fd01559ed81298859d21d4de45e92b1",
        "2a68e08ab13ad9b27e1d6d1c8663e474737ea809c3ec953efd9ad5d6084689f3",
        "2c860b6a4d1b8754d81235b64589a7f43d29a9d6f0dd0214eae0e08062b711cd",
        "33e141f2e23287ae295edf5703a6a553364c59debb8a7b6651bab5af19b4a579",
        "4de4dd2083143b3d14e99974f870c828913ac1716f520bc8d518d23bff29f2c0",
        "61576e5e2b5bfd199a9290a731eaf3f722d92377d9ae38e05c363c89f9418750",
        "64535b28084a95b1b55a7a41245a69336ff0c505d84084bf54475fac75ff8340",
        "6d4b9ee62028b80981988484e790bac1fdd5a9eb9f30022c6268397e5dd2b79f",
        "71ebf97db2ab5c22af62c644413c55763feef90e851d7c70562e1fa7c6c9bfbe",
        "91f13eced8a558c001bb5982b0d6577ec1e27ecab1c3178f28f8d67e52f06e39",
        "a0b7d2e5555d52919050b0275ede891eb2a9e3e71c218b8303cbb3acdf7ce05b",
        "ac213153a24c16a88a26a5296b917aac1d7aeb7daeb274bbe70219e3661d3f2e",
        "af7c582a4b5c0a696512f226580075eef7fc56c047ddedd94283b74ef6e24815",
        "b67df6b88173029eca4e3840861c113230e7367b9b3c8327ba85c064fdf4634f",
        "b81a996348d600d2db6f24b1cda810e42bf05e169bdbd7c931ff2cad8639629f",
        "b82e3d82e504b207a74304a2038d17dc3afa156f64fe2add39e4d2944132377d",
        "d028e780d4647250ef59d893f67bcea4f7ebad59fe7d0dc41f64fa9cb3efb4e0",
        "d83253cb66fe627ea4a342a491240126d76939b1aa16a304ff07987ba5db3d78",
        "eb1077f11f5954fe8cff01a7fcb8a969e909678e970cf8774c660d9736ebe599",
    }
)

_PROTECTED_RESOURCE_SHA256 = {
    "src/fspm_optics/resources/cad/hps/hps_1000w_fixture.raw.glb": (
        "09bff23bdb43de8c9e9d8c3422188517afad76868260cf3686a1fd277b9c4051"
    ),
    "src/fspm_optics/resources/cad/hps/hps_1000w_fixture.step": (
        "10423382d90007159b351fc417fbb94ec3a7305dd14242d59a5070967de061c7"
    ),
    "src/fspm_optics/resources/cad/proposed/led_module.raw.glb": (
        "f221556c4a47d3fe58122940a0edf26376c3b3d3f924ec29d47fec024b8372f2"
    ),
    "src/fspm_optics/resources/cad/proposed/led_module_150x150.step": (
        "e89610cd20df30adc63d1c21198438bce209b3fa429c035797533ec250eb5ab8"
    ),
    "src/fspm_optics/resources/data/conventional_led/conventional_led_8_bar.ies": (
        "67d6f93b40638b5a0534fe34fa43d78811c0c0476e1e367490a38157e4ba1cf4"
    ),
    "src/fspm_optics/resources/data/conventional_led/conventional_led_spd.csv": (
        "1820512df69f93d7325b08e27887ab2e8155efa9d536bcd06b9f1c739bf57e44"
    ),
    "src/fspm_optics/resources/data/hps/hps_1000w.ies": (
        "05f449b6d5762fd1ae459a41df2cad510a680d5d9bbb9639ca0a26d3609002a3"
    ),
    "src/fspm_optics/resources/data/hps/hps_1000w_spd.csv": (
        "d27fffb2410d30ed38c7fde6851563e0ddba6a8de824ecefe8c7624038e5e133"
    ),
    "src/fspm_optics/resources/data/proposed_cob/citizen_clu04q_1812e1/Ref_CE-1138-202209_CLU04Q-1812E1-302M2X2.ies": (
        "365c1615905475a9b728cdbfc058805504c26fdadfcc1c59741f5fbc456af0e2"
    ),
    "src/fspm_optics/resources/data/radiance/plant_optics/lettuce_rex/rex_leaf_optical_properties_digitized_v0_2.csv": (
        "50842cfc0ae26f98b184e570d603d884b797ae0ad9b04bd1b8277f5b3efd74e8"
    ),
    "src/fspm_optics/resources/data/smd/smd_3000k_spd.csv": (
        "68797d94ab11251ea777dfdf98bf478baa25fcd2874e349a1173e2b57bab03c1"
    ),
    "src/fspm_optics/resources/data/smd/smd_5000k_spd.csv": (
        "630acb83a1f83fd1723621671a83b502fc933dabcb6af109dd0b9578e5fe698b"
    ),
    "src/fspm_optics/resources/data/smd/smd_660nm_spd.csv": (
        "5a1f911fd6596e2ffd91e691561e08a245277f621617fbca51fd8df5160cd6de"
    ),
    "src/fspm_optics/resources/viewer/fixtures/conventional/conventional_led_8_bar.glb": (
        "0d640d8e20bfdc213d3722dc44652979366b7c19ef153fff10372fe66051af53"
    ),
    "src/fspm_optics/resources/viewer/fixtures/hps/hps.glb": (
        "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/centerpiece.glb": (
        "76daa0290eafb3947b30489d304287b25dab998f847ba5d5a8163775cb38c1ba"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/corner3.glb": (
        "e30f98457b1af1725843ed0c42edfc9d22516445c602f4edcc8b8ed116c7d243"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/l.glb": (
        "ea1c9ea94aa08c30a655458c0a01e74a3687e2e4333203d10961520d6634730e"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/led_module.glb": (
        "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/linear2.glb": (
        "814de19659cff0130b508f197a2194383748eee191f10927524229aabd5c288d"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/linear3.glb": (
        "e9cb018d9ce5bb6babe59044d88639c6620d6018eb22e71f6fc404c250145d93"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/linear4.glb": (
        "1f77749ae1c3dda08cf8a521734f9aea5e54b64df68b963341bdca5407ca6809"
    ),
    "src/fspm_optics/resources/viewer/fixtures/proposed/reverse_l.glb": (
        "eb87a439e99ae95ac5ad73ea96ba4de9bbcd03609e45355d4e92a8210034badd"
    ),
}

_CONVENTIONAL_NUMERICAL_FIELDS = (
    "lamp_count",
    "lumens_per_lamp",
    "candela_multiplier",
    "vertical_angle_count",
    "horizontal_angle_count",
    "photometric_type",
    "units_type",
    "fixture_width_m",
    "fixture_length_m",
    "fixture_height_m",
    "source_fixture_width",
    "source_fixture_length",
    "source_fixture_height",
    "ballast_factor",
    "future_use_factor",
    "input_watts",
    "vertical_angles_deg",
    "horizontal_angles_deg",
    "candela_by_horizontal_plane",
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_json(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return _sha256_bytes(raw)


def _sequence_identity(values: list[str]) -> str:
    raw = json.dumps(
        values,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return _sha256_bytes(raw)


def _selector(case, *, target_ppfd: float | None = 250.0) -> dict[str, object]:
    room = case.canonical_domain["supported_requested_room_orders_ft"][0]
    selector: dict[str, object] = {
        "system": case.system_id,
        "room_length_ft": room["length"],
        "room_width_ft": room["width"],
        "aisle_mode": case.aisle_enabled,
    }
    if case.system_id == CONVENTIONAL_SYSTEM_ID:
        selector["layout_mode"] = case.layout_variant
    if case.system_id != HPS_SYSTEM_ID and target_ppfd is not None:
        selector["target_ppfd"] = target_ppfd
    return selector


@cache
def _canonical_json(system: str, member: str) -> dict[str, object]:
    bundle = sorted((PRECOMPUTED_ROOT / system).rglob("*.fspm-compact"))[0]
    with zipfile.ZipFile(bundle) as archive:
        value = json.loads(archive.read(member))
    assert isinstance(value, dict)
    return value


@cache
def _prohibited_values() -> tuple[str, ...]:
    conventional_manifest = _canonical_json(
        CONVENTIONAL_SYSTEM_ID, "manifest.json"
    )
    identities = conventional_manifest["authenticated_identities"]
    assert isinstance(identities, Mapping)
    fixed_inputs = identities["fixed_plan_inputs"]
    assert isinstance(fixed_inputs, Mapping)
    compatibility = fixed_inputs["compatibility_inputs"]
    assert isinstance(compatibility, Mapping)
    fixture = compatibility["fixture"]
    source = compatibility["source"]
    assert isinstance(fixture, Mapping) and isinstance(source, Mapping)
    assets = fixture["assets"]
    assert isinstance(assets, list) and len(assets) == 1
    asset = assets[0]
    assert isinstance(asset, Mapping)

    conventional_catalog = _canonical_json(
        CONVENTIONAL_SYSTEM_ID,
        "payload/viewer/fixtures/catalog.v1.json",
    )
    groups = conventional_catalog["asset_groups"]
    assert isinstance(groups, list) and len(groups) == 1
    group = groups[0]
    assert isinstance(group, Mapping)
    matrices = group["instance_matrices"]
    assert isinstance(matrices, Mapping)

    proposed_result = _canonical_json(
        PROPOSED_SYSTEM_ID, "payload/public-result.v1.json"
    )
    metrics = proposed_result["metrics"]
    assert isinstance(metrics, Mapping)
    spectral = metrics["spectral_basis"]
    assert isinstance(spectral, Mapping)
    provenance = spectral["control_provenance"]
    assert isinstance(provenance, Mapping)
    spd = provenance["relative_spd_authority"]
    assert isinstance(spd, Mapping)

    hps_manifest = _canonical_json(HPS_SYSTEM_ID, "manifest.json")
    historical_hps_system = hps_manifest["system_id"]
    assert isinstance(historical_hps_system, str)
    assert historical_hps_system != HPS_SYSTEM_ID

    brand_token = str(asset["asset_id"]).split("-")[1]
    values = {
        brand_token,
        str(asset["asset_id"]),
        str(asset["fixture_type"]),
        str(asset["resource_path"]),
        str(source["profile_id"]),
        str(source["ies_sha256"]),
        str(group["display_asset_id"]),
        str(group["display_fixture_type"]),
        str(matrices["filename"]),
        str(spectral["id"]),
        str(spectral["label"]),
        str(spectral["source_model_id"]),
        str(spectral["spectral_distribution_id"]),
        str(spd["resource"]),
        historical_hps_system,
        historical_hps_system.split("_", 1)[0],
        "qb" + "-fsg",
        "blc" + "2107022e",
        "bel" + "ling",
        "gpm" + "-3000",
        "sp" + "ydr",
        "conventional_" + brand_token + "_unit_downward_flux",
        "sr" + "-3h47",
    }
    lowered = tuple(sorted(value.lower() for value in values if value))
    fingerprints = frozenset(_sha256_bytes(value.encode()) for value in lowered)
    assert fingerprints == _EXPECTED_DENY_FINGERPRINTS
    return lowered


def _searchable_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8").lower()
    except UnicodeDecodeError:
        return "\n".join(
            item.decode("ascii").lower() for item in _PRINTABLE.findall(raw)
        )


def _assert_anonymous(value: object) -> None:
    if isinstance(value, bytes):
        text = _searchable_text(value)
    elif isinstance(value, str):
        text = value.lower()
    else:
        text = json.dumps(value, sort_keys=True, allow_nan=False).lower()
    found = [token for token in _prohibited_values() if token in text]
    assert found == []


def _tracked_paths() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    paths = {
        REPOSITORY_ROOT / relative
        for relative in completed.stdout.decode("utf-8").split("\0")
        if relative and not relative.startswith("precomputed/")
    }
    # The gate audits itself even before the new file is added to Git's index.
    paths.add(Path(__file__).resolve())
    return tuple(sorted(paths))


def _file_inventory(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_bytes(path.read_bytes()),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _protected_inventory() -> dict[str, tuple[int, int, str]]:
    return {
        relative: (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            _sha256_bytes(path.read_bytes()),
        )
        for relative in _PROTECTED_RESOURCE_SHA256
        for path in (REPOSITORY_ROOT / relative,)
    }


def _conventional_numerical_fingerprint() -> str:
    photometry = load_approved_lm63()
    payload = {
        name: getattr(photometry, name)
        for name in _CONVENTIONAL_NUMERICAL_FIELDS
    }
    return _sha256_json(payload)


def _hps_numerical_fingerprint() -> str:
    payload = asdict(load_hps_lm63())
    payload.pop("keywords")
    return _sha256_json(payload)


def _resource_names(directory: str) -> tuple[str, ...]:
    root = PACKAGE_ROOT / "resources" / directory
    return tuple(
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_file() and path.suffix in {".css", ".html", ".js"}
        )
    )


def _assert_public_response(
    response: httpx.Response,
    *,
    expected_status: int = 200,
    audit_inbound_url: bool = True,
) -> None:
    assert response.status_code == expected_status
    assert not response.is_redirect
    _assert_anonymous(response.content)
    _assert_anonymous(dict(response.headers))
    if audit_inbound_url:
        _assert_anonymous(str(response.url))


async def _submit_cached(
    client: httpx.AsyncClient,
    selector: dict[str, object],
    *,
    idempotency_key: str,
) -> tuple[dict[str, object], httpx.Response]:
    response = await client.post(
        "/api/precomputed/playbacks",
        json=selector,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response.status_code in {200, 202}
    _assert_public_response(response, expected_status=response.status_code)
    body = response.json()
    for _attempt in range(200):
        status = await client.get(body["status_url"])
        _assert_public_response(status)
        body = status.json()
        if body["state"] in {"completed", "failed", "expired"}:
            return body, status
        await asyncio.sleep(0.01)
    raise AssertionError("playback status did not become terminal")


def test_complete_current_tree_filename_text_and_binary_surface_is_anonymous() -> None:
    prohibited = _prohibited_values()
    for path in _tracked_paths():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix().lower()
        assert [token for token in prohibited if token in relative] == []
        searchable = _searchable_text(path.read_bytes())
        found = [token for token in prohibited if token in searchable]
        assert found == [], relative


def test_protected_resources_and_lm63_numerical_contracts_are_exact() -> None:
    before = _protected_inventory()
    assert set(before) == set(_PROTECTED_RESOURCE_SHA256)
    assert {
        relative: identity[2] for relative, identity in before.items()
    } == _PROTECTED_RESOURCE_SHA256

    conventional_ies = conventional_resource_bytes(
        CONVENTIONAL_IES_RESOURCE_NAME
    )
    hps_ies = hps_resource_bytes(HPS_IES_RESOURCE_NAME)
    assert _sha256_bytes(conventional_ies) == _PROTECTED_RESOURCE_SHA256[
        "src/fspm_optics/resources/data/conventional_led/conventional_led_8_bar.ies"
    ]
    assert _sha256_bytes(hps_ies) == _PROTECTED_RESOURCE_SHA256[
        "src/fspm_optics/resources/data/hps/hps_1000w.ies"
    ]
    assert _sha256_bytes(
        conventional_resource_bytes(CONVENTIONAL_SPD_RESOURCE_NAME)
    ) == _PROTECTED_RESOURCE_SHA256[
        "src/fspm_optics/resources/data/conventional_led/conventional_led_spd.csv"
    ]
    assert _sha256_bytes(hps_resource_bytes(HPS_SPD_RESOURCE_NAME)) == (
        _PROTECTED_RESOURCE_SHA256[
            "src/fspm_optics/resources/data/hps/hps_1000w_spd.csv"
        ]
    )

    marker = b"TILT=NONE"
    assert _sha256_bytes(conventional_ies[conventional_ies.index(marker) :]) == (
        "2e777de00a1dff732153a71861ffe46480c0981e3287c7ffe05c06067f095299"
    )
    assert _sha256_bytes(hps_ies[hps_ies.index(marker) :]) == (
        "fe8b413c6d2da88c1b00b7d296c55f18c5dddbe3c8f9951169b2598cc42eef14"
    )
    assert _conventional_numerical_fingerprint() == (
        "6c27b16b92be7ff9ee944cd74dd84da6818051845f988ca7074ccd4103378948"
    )
    assert _hps_numerical_fingerprint() == (
        "198279a371673c8e16f01d4dc7831949e4b9a2c1db1d2c7615ce8012da7b7bd1"
    )

    registry_paths = {
        f"src/fspm_optics/resources/viewer/{asset.resource_path}": asset.sha256
        for asset in ASSET_REGISTRY
    }
    assert len(registry_paths) == 10
    assert {
        path: _PROTECTED_RESOURCE_SHA256[path] for path in registry_paths
    } == registry_paths
    assert _protected_inventory() == before


def test_immutable_precomputed_inventory_identities_and_all_loads_are_exact() -> None:
    plan = build_committed_playback_plan()
    inventory = _file_inventory(PRECOMPUTED_ROOT)
    expected_paths = {
        "fixed-sweep-progress.v1.json",
        *(case.storage_relative_path for case in plan.cases),
    }
    assert len(inventory) == 25
    assert sum(item["path"].endswith(".fspm-compact") for item in inventory) == 24
    assert {item["path"] for item in inventory} == expected_paths
    assert _sha256_json(inventory) == _EXPECTED_PRECOMPUTED_INVENTORY_SHA256

    assert plan.plan_identity_sha256 == HISTORICAL_PLAN_IDENTITY_SHA256 == (
        "114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416"
    )
    assert len(plan.cases) == 24
    assert _sequence_identity([case.case_id for case in plan.cases]) == (
        "a6a13c58dc0c3b535de3444eee0b7a267d625d9b8130474d9715644d5fe39ffd"
    )
    assert _sequence_identity(
        [case.artifact_case_id_sha256 for case in plan.cases]
    ) == HISTORICAL_CASE_DIGEST_SEQUENCE_IDENTITY_SHA256
    assert _sequence_identity(
        [case.bundle_identity_sha256 for case in plan.cases]
    ) == HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256

    status = inspect_committed_playback_catalog(PRECOMPUTED_ROOT, plan=plan)
    assert (status.valid_count, status.invalid_count, status.remaining_count) == (
        24,
        0,
        0,
    )
    loaded_case_ids: list[str] = []
    loaded_bundle_ids: list[str] = []
    for case in plan.cases:
        playback = load_committed_case_bundle(
            case.output_path(PRECOMPUTED_ROOT),
            case,
            plan.plan_identity_sha256,
        )
        fixed_plan = playback.manifest["run_configuration"]["fixed_plan"]
        loaded_case_ids.append(fixed_plan["case_id"])
        loaded_bundle_ids.append(playback.manifest["bundle_identity_sha256"])
    assert _sequence_identity(loaded_case_ids) == (
        HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256
    )
    assert [
        _sha256_bytes(case_id.encode("utf-8")) for case_id in loaded_case_ids
    ] == [case.artifact_case_id_sha256 for case in plan.cases]
    assert loaded_bundle_ids == [case.bundle_identity_sha256 for case in plan.cases]


def test_public_asgi_surface_and_behavioral_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSPM_BASE_BUNDLE_CACHE_SIZE", "24")
    monkeypatch.setenv("FSPM_PLAYBACK_CACHE_SIZE", "64")
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)
    plan = build_committed_playback_plan()

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client,
        ):
            for path in (
                "/",
                "/health/live",
                "/health/ready",
                "/api/capabilities",
                "/api/precomputed/availability",
                *(f"/static/{name}" for name in STATIC_ASSETS),
            ):
                _assert_public_response(await client.get(path))

            capabilities = (await client.get("/api/capabilities")).json()
            assert capabilities["precomputed_playback"]["systems"] == list(
                SUPPORTED_SYSTEM_IDS
            )
            assert capabilities["precomputed_playback"][
                "fspm_target_tolerance"
            ] == {
                "enabled": True,
                "supported_systems": list(SUPPORTED_SYSTEM_IDS),
                "type": "number",
                "default": 75.0,
                "finite": True,
                "strictly_positive": True,
            }
            assert capabilities["live_simulation"] == {
                "enabled": False,
                "systems": [],
            }
            assert capabilities["precomputed_playback"][
                "lighting_target_modes"
            ][HPS_SYSTEM_ID] == []
            availability = (
                await client.get("/api/precomputed/availability")
            ).json()
            assert (
                availability["case_count"],
                availability["valid_case_count"],
                availability["missing_case_count"],
                availability["invalid_case_count"],
            ) == (24, 24, 0, 0)
            assert availability["plan_identity_sha256"] == (
                HISTORICAL_PLAN_IDENTITY_SHA256
            )

            representatives = {}
            for case in plan.cases:
                selector = _selector(case)
                record = app.state.precomputed.load(selector)
                body, _status_response = await _submit_cached(
                    client,
                    selector,
                    idempotency_key=f"final-gate-{case.ordinal:02d}",
                )
                assert body["state"] == "completed"
                result = body["result"]
                assert result["system_id"] == case.system_id
                assert result["case_id"] == case.case_id
                assert result["bundle_identity_sha256"] == (
                    case.bundle_identity_sha256
                )
                _assert_anonymous(result)

                playback_id = result["run_id"]
                assert playback_id == record.playback_id
                metrics = await client.get(result["metrics_url"])
                manifest = await client.get(result["manifest_url"])
                scene = await client.get(
                    f"/precomputed/{playback_id}/viewer/scene.v1.json"
                )
                fixture_catalog = await client.get(
                    f"/precomputed/{playback_id}/viewer/fixtures/catalog.v1.json"
                )
                for response in (metrics, manifest, scene, fixture_catalog):
                    _assert_public_response(response)
                assert manifest.json()["source_runtime_required"] is False

                representatives.setdefault(case.system_id, (case, record, result))
                if case.system_id in {CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID}:
                    public_catalog = fixture_catalog.json()
                    group = public_catalog["asset_groups"][0]
                    asset_path = f"fixtures/{group['asset']['filename']}"
                    transforms_path = (
                        f"fixtures/{group['instance_matrices']['filename']}"
                    )
                    asset_response = await client.get(
                        f"/precomputed/{playback_id}/viewer/{asset_path}"
                    )
                    transforms_response = await client.get(
                        f"/precomputed/{playback_id}/viewer/{transforms_path}"
                    )
                    for response in (asset_response, transforms_response):
                        _assert_public_response(response)
                    assert asset_response.content == record.viewer_artifact(
                        asset_path
                    ).data
                    assert transforms_response.content == record.viewer_artifact(
                        transforms_path
                    ).data
                    assert _sha256_bytes(asset_response.content) == group["asset"][
                        "sha256"
                    ]
                    assert _sha256_bytes(transforms_response.content) == group[
                        "instance_matrices"
                    ]["sha256"]

            first_record = next(iter(representatives.values()))[1]
            for name in _resource_names("viewer"):
                _assert_public_response(
                    await client.get(
                        f"/precomputed/{first_record.playback_id}/viewer/{name}"
                    )
                )
            for name in _resource_names("scatter"):
                _assert_public_response(
                    await client.get(
                        f"/precomputed/{first_record.playback_id}/scatter/{name}"
                    )
                )

            for system_id, (_case, record, result) in representatives.items():
                for url in (
                    result["ppfd_csv_url"],
                    (
                        f"/api/precomputed/playbacks/{record.playback_id}/artifacts/"
                        "ppfd-scatter.f32le.bin"
                    ),
                    (
                        f"/precomputed/{record.playback_id}/viewer/ppfd-heatmap/"
                        "ppfd-scatter.f32le.bin"
                    ),
                    result["ppfd_scatter_viewer_url"],
                ):
                    _assert_public_response(await client.get(url))
                assert system_id in SUPPORTED_SYSTEM_IDS

            assert SYSTEM_DISPLAY_NAMES == {
                PROPOSED_SYSTEM_ID: "Proposed LED System",
                CONVENTIONAL_SYSTEM_ID: "Conventional LED System",
                HPS_SYSTEM_ID: "1000W HPS System",
            }
            proposed_record = representatives[PROPOSED_SYSTEM_ID][1]
            assert proposed_record.metrics()["spectral_basis"] == {
                **proposed_record.metrics()["spectral_basis"],
                "id": "conventional_led_control",
                "label": "Conventional LED spectrum (controlled A/B)",
            }

            conventional_case = representatives[CONVENTIONAL_SYSTEM_ID][0]
            scaled_selector = _selector(conventional_case, target_ppfd=125.0)
            app.state.precomputed.load(scaled_selector)
            scaled_body, _scaled_status = await _submit_cached(
                client,
                scaled_selector,
                idempotency_key="final-gate-conventional-scaling",
            )
            assert scaled_body["state"] == "completed"
            scaled_metrics = await client.get(
                scaled_body["result"]["metrics_url"]
            )
            _assert_public_response(scaled_metrics)
            assert scaled_metrics.json()[
                "achieved_mean_ppfd_umol_m2_s"
            ] == pytest.approx(125.0)
            assert scaled_body["result"]["target_adjustment"][
                "output_limited"
            ] is False

            hps_case = representatives[HPS_SYSTEM_ID][0]
            hps_override = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(hps_case) | {"target_ppfd": 250.0},
            )
            _assert_public_response(hps_override, expected_status=422)

            invalid = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(hps_case) | {"system": "invalid-system"},
            )
            _assert_public_response(invalid, expected_status=422)

            historical_hps = _canonical_json(HPS_SYSTEM_ID, "manifest.json")[
                "system_id"
            ]
            assert isinstance(historical_hps, str)
            alias = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(hps_case) | {"system": historical_hps},
            )
            _assert_public_response(alias, expected_status=422)

            proposed_historical = _canonical_json(
                PROPOSED_SYSTEM_ID, "payload/public-result.v1.json"
            )["metrics"]["spectral_basis"]["id"]
            historical_control = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(representatives[PROPOSED_SYSTEM_ID][0])
                | {"spectral_basis": proposed_historical},
            )
            _assert_public_response(historical_control, expected_status=422)

            for system_id in (CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID):
                record = representatives[system_id][1]
                canonical_catalog = json.loads(
                    record.playback.canonical.payloads["fixture_catalog"]
                )
                historical_group = canonical_catalog["asset_groups"][0]
                for historical_name in (
                    historical_group["asset"]["filename"],
                    historical_group["instance_matrices"]["filename"],
                ):
                    rejected = await client.get(
                        f"/precomputed/{record.playback_id}/viewer/fixtures/"
                        f"{historical_name}"
                    )
                    # The inbound probe intentionally contains a recovered legacy
                    # path; only generated/public URLs are part of the URL audit.
                    _assert_public_response(
                        rejected,
                        expected_status=404,
                        audit_inbound_url=False,
                    )

            for system_id in (CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID):
                unavailable = await client.post(
                    "/api/runs",
                    json={"system": system_id},
                )
                _assert_public_response(unavailable, expected_status=404)

    asyncio.run(asyncio.wait_for(scenario(), timeout=170.0))


def test_proposed_cob_request_and_package_data_graph_are_unchanged() -> None:
    payload = {
        "system": PROPOSED_SYSTEM_ID,
        "target_ppfd": 500.0,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "mounting_height_in": 18.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
        "proposed_source_mode": COB_SOURCE_MODE,
        "lighting_target_mode": "target_capped",
    }
    request = parse_run_request(payload)
    assert isinstance(request, ProposedRunRequest)
    assert request.source_mode is ProposedSourceMode.COB_SOURCE_SHAPE_SURROGATE
    assert request.to_dict()["proposed_source_mode"] == COB_SOURCE_MODE
    assert request.to_dict()["lighting_target_mode"] == "target_capped"
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        ConventionalRunRequest.from_payload(
            payload
            | {
                "system": CONVENTIONAL_SYSTEM_ID,
                "layout_mode": "practical",
            }
        )

    configuration = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    patterns = configuration["tool"]["setuptools"]["package-data"][
        "fspm_optics"
    ]
    assert isinstance(patterns, list)
    packaged: set[str] = set()
    for pattern in patterns:
        matches = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in PACKAGE_ROOT.glob(pattern)
            if path.is_file()
        }
        assert matches, pattern
        packaged.update(matches)

    required = {
        "resources/data/conventional_led/conventional_led_8_bar.ies",
        "resources/data/conventional_led/conventional_led_spd.csv",
        "resources/data/hps/hps_1000w.ies",
        "resources/data/hps/hps_1000w_spd.csv",
        "resources/web/public-index.html",
        *(
            (
                f"resources/web/{name}"
                if (PACKAGE_ROOT / "resources/web" / name).is_file()
                else f"resources/viewer/{name}"
            )
            for name in STATIC_ASSETS
        ),
        *(f"resources/viewer/{name}" for name in _resource_names("viewer")),
        *(f"resources/scatter/{name}" for name in _resource_names("scatter")),
        *(
            f"resources/viewer/{asset.resource_path}"
            for asset in ASSET_REGISTRY
        ),
    }
    assert required <= packaged
    assert all((PACKAGE_ROOT / relative).is_file() for relative in required)
    _assert_anonymous(patterns)
    _assert_anonymous(sorted(packaged))
    for name in STATIC_ASSETS:
        web_path = PACKAGE_ROOT / "resources/web" / name
        path = web_path if web_path.is_file() else PACKAGE_ROOT / "resources/viewer" / name
        _assert_anonymous(path.read_bytes())


def test_live_capability_boundary_enables_all_three_canonical_systems(
    tmp_path: Path,
) -> None:
    native_calls: list[object] = []

    def forbidden_native(**kwargs: object):
        native_calls.append(kwargs)
        raise AssertionError("capability inspection submitted native work")

    app = create_app(
        runtime_root=tmp_path / "live-capability-runtime",
        precomputed_root=PRECOMPUTED_ROOT,
        run_executor=forbidden_native,
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client,
        ):
            response = await client.get("/api/capabilities")
            _assert_public_response(response)
            assert response.json()["live_simulation"] == {
                "enabled": True,
                "systems": [
                    PROPOSED_SYSTEM_ID,
                    CONVENTIONAL_SYSTEM_ID,
                    HPS_SYSTEM_ID,
                ],
            }

    asyncio.run(scenario())
    assert native_calls == []
