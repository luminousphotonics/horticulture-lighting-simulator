import { copyFile, mkdir, readFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const expectedVersion = "0.184.0";
const repository = dirname(dirname(fileURLToPath(import.meta.url)));
const packageRoot = join(repository, "node_modules", "three");
const packageJson = JSON.parse(
  await readFile(join(packageRoot, "package.json"), "utf8"),
);
if (packageJson.version !== expectedVersion) {
  throw new Error(
    `Expected three@${expectedVersion}, found ${packageJson.version ?? "missing"}.`,
  );
}

const destination = join(
  repository,
  "src",
  "fspm_optics",
  "resources",
  "viewer",
  "vendor",
);
await rm(destination, { force: true, recursive: true });
await mkdir(join(destination, "addons", "controls"), { recursive: true });
await mkdir(join(destination, "addons", "environments"), { recursive: true });
await mkdir(join(destination, "addons", "loaders"), { recursive: true });
await mkdir(join(destination, "addons", "utils"), { recursive: true });

const copies = [
  ["build/three.core.js", "three.core.js"],
  ["build/three.module.js", "three.module.js"],
  [
    "examples/jsm/controls/OrbitControls.js",
    "addons/controls/OrbitControls.js",
  ],
  [
    "examples/jsm/environments/RoomEnvironment.js",
    "addons/environments/RoomEnvironment.js",
  ],
  [
    "examples/jsm/loaders/GLTFLoader.js",
    "addons/loaders/GLTFLoader.js",
  ],
  [
    "examples/jsm/utils/BufferGeometryUtils.js",
    "addons/utils/BufferGeometryUtils.js",
  ],
  [
    "examples/jsm/utils/SkeletonUtils.js",
    "addons/utils/SkeletonUtils.js",
  ],
  ["LICENSE", "THREE-LICENSE.txt"],
];
for (const [source, target] of copies) {
  await copyFile(join(packageRoot, source), join(destination, target));
}

console.log(
  `Vendored official three@${expectedVersion} core, controls, RoomEnvironment, GLTF loader, and loader utilities.`,
);
