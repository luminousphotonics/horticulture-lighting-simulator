from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "src/fspm_optics/resources/viewer"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_official_three_0p184_loader_vendor_contract_is_local_only() -> None:
    vendor = _read("scripts/vendor-three.mjs")
    html = _read("src/fspm_optics/resources/viewer/index.html")
    renderer = _read("src/fspm_optics/resources/viewer/fixture-renderer.js")

    assert 'expectedVersion = "0.184.0"' in vendor
    assert "examples/jsm/environments/RoomEnvironment.js" in vendor
    assert "examples/jsm/loaders/GLTFLoader.js" in vendor
    assert "examples/jsm/utils/BufferGeometryUtils.js" in vendor
    assert "examples/jsm/utils/SkeletonUtils.js" in vendor
    assert 'from "three/addons/loaders/GLTFLoader.js"' in renderer
    assert '"three/addons/": "./vendor/addons/"' in html
    assert "http://" not in html + renderer and "https://" not in html + renderer


def test_local_room_environment_is_generated_once_and_disposed_with_session() -> None:
    vendor = _read("scripts/vendor-three.mjs")
    environment = _read(
        "src/fspm_optics/resources/viewer/vendor/addons/environments/RoomEnvironment.js"
    )
    renderer = _read("src/fspm_optics/resources/viewer/renderer.js")
    main = _read("src/fspm_optics/resources/viewer/main.js")

    assert 'from "three/addons/environments/RoomEnvironment.js"' in renderer
    assert "from 'three';" in environment
    assert "external environment" not in renderer.lower()
    assert "RGBELoader" not in renderer + main
    assert "TextureLoader" not in renderer + main
    assert "fetch(" not in renderer
    assert renderer.count("new RoomEnvironment()") == 1
    assert renderer.count("new THREE.PMREMGenerator(renderer)") == 1
    assert renderer.count("pmremGenerator.fromScene(roomEnvironment)") == 1
    assert main.count("createInspectionEnvironment(renderer, scene)") == 1
    assert "scene.environment = renderTarget.texture" in renderer
    assert "scene.environmentIntensity = 1.0" in renderer
    assert "scene.background" not in renderer + main
    assert "roomEnvironment.dispose()" in renderer
    assert "pmremGenerator?.dispose()" in renderer
    assert "scene.environment = null" in renderer
    assert "renderTarget.dispose()" in renderer
    assert "inspectionEnvironment?.dispose()" in main
    assert "pagehide" in main
    assert "examples/jsm/environments/RoomEnvironment.js" in vendor


def test_catalog_and_every_binary_are_verified_before_parse_or_matrix_use() -> None:
    artifacts = _read("src/fspm_optics/resources/viewer/fixture-artifacts.js")
    renderer = _read("src/fspm_optics/resources/viewer/fixture-renderer.js")

    assert artifacts.index("catalogBytes.byteLength") < artifacts.index(
        'verifyHash(\n    catalogBytes'
    )
    assert artifacts.index('verifyHash(\n    catalogBytes') < artifacts.index(
        "catalog.asset_groups.map"
    )
    assert artifacts.index("fetchArtifact(catalogBase, group.asset)") < artifacts.index(
        "validateEmbeddedFixtureGlb(glbBytes"
    )
    assert artifacts.index("fetchArtifact(catalogBase, {") < artifacts.index(
        "parseFixtureMatrices(matrixBytes"
    )
    assert renderer.count("loader.parse(") == 1
    assert "secondary resource URIs are prohibited" in artifacts
    assert "setURLModifier" in renderer


def test_asset_root_baking_and_positive_instance_parity_are_explicit() -> None:
    renderer = _read("src/fspm_optics/resources/viewer/fixture-renderer.js")

    assert renderer.count("multiplyMatrices(") == 1
    assert "multiplyMatrices(serverMatrix, createAssetRootReflection())" in renderer
    assert "sourceMesh.matrixWorld" in renderer
    assert "sourceMesh.position" not in renderer
    assert "sourceMesh.rotation" not in renderer
    assert "sourceMesh.scale" not in renderer
    assert "centroid" not in renderer.lower()
    assert "proximity" not in renderer.lower()
    assert "promoteTransformAttributesToFloat32(geometry)" in renderer
    assert "geometry.applyMatrix4(authoredPostRootWorldMatrix)" in renderer
    assert "geometry.applyMatrix4(createAssetRootReflection())" in renderer
    assert "reverseTriangleWinding(geometry)" in renderer
    assert "flipTangentHandedness(geometry)" in renderer
    assert "instances.setMatrixAt(instanceId, submittedMatrix)" in renderer
    assert "setTransformedPrimitiveBounds(" in renderer
    assert "transformedBox, geometry, submittedMatrix" in renderer
    assert "new THREE.Matrix4().fromArray" in renderer
    assert "fixtureMatrixParity(submittedMatrix) !== 1" in renderer
    assert "composeFixturePrimitiveMatrix" not in renderer
    assert "decompose(" not in renderer
    assert "source.applyMatrix4" not in renderer


def test_all_proposed_conventional_and_hps_display_assets_share_one_renderer() -> None:
    artifacts = _read("src/fspm_optics/resources/viewer/fixture-artifacts.js")
    renderer = _read("src/fspm_optics/resources/viewer/fixture-renderer.js")

    for asset_id in (
        "proposed-centerpiece-v1",
        "proposed-linear2-v1",
        "proposed-linear3-v1",
        "proposed-corner3-v1",
        "proposed-linear4-v1",
        "proposed-l-v1",
        "proposed-reverse-l-v1",
        "proposed-led-module-v1",
        "conventional-led-8-bar-v1",
        "hps-housing-v3",
    ):
        assert asset_id in artifacts
    assert "new THREE.InstancedMesh" in renderer
    assert "primitive.material" in renderer
    assert "geometry,\n              primitive.material," in renderer
    assert "primitive.material." not in renderer
    assert "alphaModes" in artifacts and '["BLEND", "OPAQUE"]' in artifacts


def test_draw_calls_scale_with_source_primitive_parity_groups() -> None:
    renderer = _read("src/fspm_optics/resources/viewer/fixture-renderer.js")
    main = _read("src/fspm_optics/resources/viewer/main.js")

    assert renderer.count("new THREE.InstancedMesh") == 3
    assert "assetGroup.matrices.count" in renderer
    assert "partitionFixtureMatrixIndices" in renderer
    assert "partition.sourceInstanceIds.length" in renderer
    assert "primitiveCount += 1" in renderer
    assert "primitiveDrawCalls: primitiveCount" in renderer
    assert "renderer.info.render.calls" not in main
    assert "plantDrawCalls" not in main
    assert "fixtureDrawCalls" not in main


def test_decimal_room_floor_perimeter_and_combined_camera_bounds_are_shared() -> None:
    renderer = _read("src/fspm_optics/resources/viewer/renderer.js")
    main = _read("src/fspm_optics/resources/viewer/main.js")
    fixture_artifacts = _read(
        "src/fspm_optics/resources/viewer/fixture-artifacts.js"
    )

    assert "authoritative-room-reference-surface" in renderer
    assert "authoritative-room-perimeter" in renderer
    assert "referencePlane.vertices_xyz.flat()" in renderer
    assert "combineAuthoritativeBounds" in main
    for authority in (
        "scene.room_bounds",
        "scene.plant_bounds",
        "scene.reference_plane.vertices_xyz",
        "fixtureBounds.minimum_xyz",
        "fixtureBounds.maximum_xyz",
    ):
        assert authority in fixture_artifacts
    assert "canopy plane" not in (renderer + main).lower()


def test_fixture_visibility_picking_disposal_and_idle_rendering_contracts() -> None:
    html = _read("src/fspm_optics/resources/viewer/index.html")
    main = _read("src/fspm_optics/resources/viewer/main.js")
    fixture_renderer = _read(
        "src/fspm_optics/resources/viewer/fixture-renderer.js"
    )

    assert 'data-control="fixtures" type="checkbox" checked' in html
    assert "layers.set(FIXTURE_RENDER_LAYER)" in fixture_renderer
    assert "camera.layers.enable(FIXTURE_RENDER_LAYER)" in main
    assert "raycaster.layers.set(0)" in main
    assert "intersectObject(surface, false)" in main
    assert "pagehide" in main and "fixtureDisplay?.dispose()" in main
    assert "fixtureHeightController?.dispose()" in main
    assert "ownedGeometries" in fixture_renderer
    assert "ownedMaterials" in fixture_renderer
    assert "ownedTextures" in fixture_renderer
    assert main.count("requestAnimationFrame(") == 1
    assert "setAnimationLoop" not in main


def test_neutral_camera_relative_inspection_lighting_preserves_idle_rendering() -> None:
    renderer = _read("src/fspm_optics/resources/viewer/renderer.js")
    main = _read("src/fspm_optics/resources/viewer/main.js")

    assert "new THREE.AmbientLight(0xffffff, 0.18)" in renderer
    assert "new THREE.HemisphereLight(0xffffff, 0xb8b8b8, 0.28)" in renderer
    assert "new THREE.DirectionalLight(0xffffff, 0.85)" in renderer
    assert 'cameraKey.name = "camera-relative-inspection-key"' in renderer
    assert "camera.add(cameraKey, cameraTarget)" in renderer
    assert "camera.remove(cameraKey, cameraTarget)" in renderer
    assert "cameraKey.castShadow = false" in renderer
    assert "inspectionLightRig?.dispose()" in main
    assert main.count("requestAnimationFrame(") == 1
    for forbidden in ("material.color", "material.emissive", "toneMapping"):
        assert forbidden not in renderer + main


def test_package_and_success_inventories_require_loaders_and_all_ten_glbs() -> None:
    package = _read("pyproject.toml")
    publisher = _read("src/fspm_optics/viewer/publish.py")
    success = _read("src/fspm_optics/application/proposed.py")

    for pattern in (
        "resources/viewer/vendor/addons/environments/*.js",
        "resources/viewer/vendor/addons/loaders/*.js",
        "resources/viewer/vendor/addons/utils/*.js",
        "resources/viewer/fixtures/proposed/*.glb",
        "resources/viewer/fixtures/conventional/*.glb",
        "resources/viewer/fixtures/hps/*.glb",
    ):
        assert pattern in package
    for resource in (
        "fixture-height-controller.js",
        "ppfd-heatmap.js",
        "vendor/addons/environments/RoomEnvironment.js",
        "vendor/addons/loaders/GLTFLoader.js",
        "vendor/addons/utils/BufferGeometryUtils.js",
        "vendor/addons/utils/SkeletonUtils.js",
    ):
        assert resource in publisher
        assert f"plant-layout-viewer/{resource}" in success
    assert len(tuple((VIEWER / "fixtures").glob("*/*.glb"))) == 10


def test_browser_fixture_code_has_no_physics_or_unsupported_controls() -> None:
    # Keep this ownership boundary scoped to fixture loading/rendering. Other
    # viewer modules may display authenticated scientific fields independently.
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            VIEWER / "fixture-artifacts.js",
            VIEWER / "fixture-renderer.js",
        )
    ).lower()
    for forbidden in (
        "ppfd",
        "radiance",
        "heatmap",
        "scatter",
        "fixture-scale",
        "fixture-rotation",
        "fixture-lod",
    ):
        assert forbidden not in combined
