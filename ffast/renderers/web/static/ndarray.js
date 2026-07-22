/**
 * Decode the wire ndarray envelope (ADR 0045 Phase 3).
 *
 * The server ships numpy arrays as `{__ndarray__:true, dtype, shape, data}`
 * where `data` is the raw `array.tobytes()` bytes (see
 * `ffast/protocol/rpc.py` `_encode_array`). This is the browser twin of that
 * module's `_decode_array`: reinterpret the bytes as the right typed array and
 * carry the shape. No numpy on the client, so multi-dimensional arrays keep a
 * flat typed array plus their `shape`; the panel code slices rows itself
 * (`ndRow`) — the only shapes we handle are 1-D (per-frame metrics) and 2-D
 * (`(2, G)` density curves, `(N_elements, 2, G)` grouped curves).
 *
 * Both the server (numpy `tobytes()`) and every browser this runs in are
 * little-endian, matching JS typed-array byte order — no byte-swap needed.
 */

/** @typedef {{__ndarray__: boolean, dtype: string, shape: number[], data: Uint8Array}} WireNdarray */

/** dtype string (numpy `str(dtype)`) → the JS typed-array constructor. */
const _CTORS = {
  float64: Float64Array,
  float32: Float32Array,
  int64: null,   // BigInt64Array — widened to Number below
  uint64: null,  // BigUint64Array — widened to Number below
  int32: Int32Array,
  uint32: Uint32Array,
  int16: Int16Array,
  uint16: Uint16Array,
  int8: Int8Array,
  uint8: Uint8Array,
  bool: Uint8Array,   // numpy bool_ is one byte per element
};

/**
 * Decode one wire ndarray into a flat JS number array + its shape.
 * @param {WireNdarray|null|undefined} v
 * @returns {{values: number[], shape: number[]}|null} null when absent/malformed.
 */
export function decodeNdarray(v) {
  if (!v || !v.__ndarray__ || !v.data) return null;
  const bytes = v.data instanceof Uint8Array ? v.data : new Uint8Array(v.data);
  // Copy into a fresh, element-aligned ArrayBuffer: msgpack hands back a
  // Uint8Array view at an arbitrary byteOffset, but a Float64Array (etc.)
  // constructor requires its buffer offset to be a multiple of the element
  // size. `.slice()` gives a zero-offset copy.
  const buf = bytes.slice().buffer;
  const shape = Array.isArray(v.shape) ? v.shape.map(Number) : [];

  if (v.dtype === 'int64' || v.dtype === 'uint64') {
    const big = new (v.dtype === 'int64' ? BigInt64Array : BigUint64Array)(buf);
    return { values: Array.from(big, (x) => Number(x)), shape };
  }
  const Ctor = _CTORS[v.dtype];
  if (!Ctor) {
    console.warn(`ndarray: unsupported dtype ${v.dtype}`);
    return null;
  }
  const typed = new Ctor(buf);
  return { values: Array.from(typed), shape };
}
