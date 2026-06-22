import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const threeRoot = join(repoRoot, "node_modules", "three");
const vendorRoot = join(repoRoot, "src", "rad_rebuild", "web", "static", "vendor", "three");

const copies = [
  ["build/three.core.js", "three.core.js", null],
  ["build/three.module.js", "three.module.js", null],
  ["examples/jsm/controls/OrbitControls.js", "controls/OrbitControls.js", "../three.module.js"],
  ["examples/jsm/loaders/GLTFLoader.js", "loaders/GLTFLoader.js", "../three.module.js"],
  ["examples/jsm/utils/BufferGeometryUtils.js", "utils/BufferGeometryUtils.js", "../three.module.js"],
  ["examples/jsm/utils/SkeletonUtils.js", "utils/SkeletonUtils.js", "../three.module.js"],
  ["LICENSE", "LICENSE", null],
];

async function copyAsset([sourceRel, targetRel, threeImport]) {
  const source = join(threeRoot, sourceRel);
  const target = join(vendorRoot, targetRel);
  await mkdir(dirname(target), { recursive: true });
  let content = await readFile(source, "utf8");
  if (threeImport) {
    content = content.replaceAll("from 'three'", `from '${threeImport}'`);
  }
  await writeFile(target, content, "utf8");
}

await Promise.all(copies.map(copyAsset));
