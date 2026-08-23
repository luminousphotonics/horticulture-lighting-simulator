export const RECEIVER_COUNT = 384;
export const RECEIVER_STRIDE_BYTES = 6 * 4;

export function parseReceiverBuffer(bytes, expectedCount = RECEIVER_COUNT) {
  if (!(bytes instanceof ArrayBuffer)) {
    throw new Error("Receiver artifact must be an ArrayBuffer.");
  }
  const expectedBytes = expectedCount * RECEIVER_STRIDE_BYTES;
  if (bytes.byteLength !== expectedBytes) {
    throw new Error(
      `Receiver artifact length ${bytes.byteLength} does not match ${expectedBytes}.`,
    );
  }
  const view = new DataView(bytes);
  const positions = new Float32Array(expectedCount * 3);
  const normals = new Float32Array(expectedCount * 3);
  for (let receiver = 0; receiver < expectedCount; receiver += 1) {
    const source = receiver * RECEIVER_STRIDE_BYTES;
    const target = receiver * 3;
    for (let component = 0; component < 3; component += 1) {
      positions[target + component] = view.getFloat32(source + component * 4, true);
      normals[target + component] = view.getFloat32(source + (component + 3) * 4, true);
    }
  }
  return Object.freeze({ count: expectedCount, normals, positions });
}

export function buildNormalSegments(receiverData, length = 0.006) {
  if (!receiverData || receiverData.positions.length !== receiverData.count * 3
      || receiverData.normals.length !== receiverData.count * 3) {
    throw new Error("Receiver data is incompatible.");
  }
  const segments = new Float32Array(receiverData.count * 6);
  for (let index = 0; index < receiverData.count; index += 1) {
    const source = index * 3;
    const target = index * 6;
    for (let axis = 0; axis < 3; axis += 1) {
      const position = receiverData.positions[source + axis];
      segments[target + axis] = position;
      segments[target + axis + 3] = position
        + receiverData.normals[source + axis] * length;
    }
  }
  return segments;
}
