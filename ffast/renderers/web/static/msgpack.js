/**
 * Minimal msgpack codec.
 *
 * Handles all types produced by Python's msgpack.packb(use_bin_type=True).
 */

export const msgpack = (() => {
  const te = new TextEncoder();
  const td = new TextDecoder();

  function encodeValue(v, out) {
    if (v === null || v === undefined) { out.push(0xc0); return; }
    if (v === false) { out.push(0xc2); return; }
    if (v === true)  { out.push(0xc3); return; }
    if (typeof v === 'number') {
      if (Number.isInteger(v)) {
        encodeInt(v, out);
      } else {
        out.push(0xcb);
        const b = new ArrayBuffer(8);
        new DataView(b).setFloat64(0, v, false);
        for (const byte of new Uint8Array(b)) out.push(byte);
      }
      return;
    }
    if (typeof v === 'string') { encodeStr(v, out); return; }
    if (v instanceof Uint8Array || v instanceof ArrayBuffer) {
      const arr = v instanceof ArrayBuffer ? new Uint8Array(v) : v;
      encodeBin(arr, out); return;
    }
    if (Array.isArray(v)) { encodeArray(v, out); return; }
    if (typeof v === 'object') { encodeMap(v, out); }
  }

  function encodeInt(n, out) {
    if (n >= 0) {
      if (n < 128)         { out.push(n); return; }
      if (n < 256)         { out.push(0xcc, n); return; }
      if (n < 65536)       { out.push(0xcd, n >> 8, n & 0xff); return; }
      if (n < 4294967296)  {
        out.push(0xce, (n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff);
        return;
      }
      // 64-bit: approximate via float64
      out.push(0xcb);
      const b = new ArrayBuffer(8);
      new DataView(b).setFloat64(0, n, false);
      for (const byte of new Uint8Array(b)) out.push(byte);
    } else {
      if (n >= -32)   { out.push(n & 0xff); return; }
      if (n >= -128)  { out.push(0xd0, n & 0xff); return; }
      if (n >= -32768){ out.push(0xd1, (n >> 8) & 0xff, n & 0xff); return; }
      out.push(0xd2, (n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff);
    }
  }

  function encodeStr(s, out) {
    const bytes = te.encode(s);
    const len = bytes.length;
    if (len < 32)    { out.push(0xa0 | len); }
    else if (len < 256)   { out.push(0xd9, len); }
    else if (len < 65536) { out.push(0xda, len >> 8, len & 0xff); }
    else { out.push(0xdb, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const b of bytes) out.push(b);
  }

  function encodeBin(arr, out) {
    const len = arr.length;
    if (len < 256)   { out.push(0xc4, len); }
    else if (len < 65536) { out.push(0xc5, len >> 8, len & 0xff); }
    else { out.push(0xc6, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const b of arr) out.push(b);
  }

  function encodeArray(arr, out) {
    const len = arr.length;
    if (len < 16)    { out.push(0x90 | len); }
    else if (len < 65536) { out.push(0xdc, len >> 8, len & 0xff); }
    else { out.push(0xdd, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const item of arr) encodeValue(item, out);
  }

  function encodeMap(obj, out) {
    const keys = Object.keys(obj);
    const len = keys.length;
    if (len < 16)    { out.push(0x80 | len); }
    else if (len < 65536) { out.push(0xde, len >> 8, len & 0xff); }
    else { out.push(0xdf, (len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff); }
    for (const k of keys) { encodeStr(k, out); encodeValue(obj[k], out); }
  }

  function encode(value) {
    const out = [];
    encodeValue(value, out);
    return new Uint8Array(out);
  }

  function decode(buffer) {
    const view = new DataView(buffer instanceof ArrayBuffer ? buffer : buffer.buffer);
    let pos = buffer instanceof ArrayBuffer ? 0 : (buffer.byteOffset || 0);

    function readByte()  { return view.getUint8(pos++); }
    function readU8()    { return view.getUint8(pos++); }
    function readU16()   { const v = view.getUint16(pos, false); pos += 2; return v; }
    function readU32()   { const v = view.getUint32(pos, false); pos += 4; return v; }
    function readI8()    { const v = view.getInt8(pos); pos++; return v; }
    function readI16()   { const v = view.getInt16(pos, false); pos += 2; return v; }
    function readI32()   { const v = view.getInt32(pos, false); pos += 4; return v; }
    function readF32()   { const v = view.getFloat32(pos, false); pos += 4; return v; }
    function readF64()   { const v = view.getFloat64(pos, false); pos += 8; return v; }
    function readBytes(n){ const b = new Uint8Array(view.buffer, pos, n); pos += n; return b; }
    function readStr(n)  { return td.decode(readBytes(n)); }

    function readArray(n) { const a = []; for (let i = 0; i < n; i++) a.push(readValue()); return a; }
    function readMap(n)   {
      const o = {};
      for (let i = 0; i < n; i++) { const k = readValue(); o[k] = readValue(); }
      return o;
    }

    function readValue() {
      const b = readByte();
      if (b === 0xc0) return null;
      if (b === 0xc2) return false;
      if (b === 0xc3) return true;
      if ((b & 0x80) === 0)   return b;                  // positive fixint
      if ((b & 0xe0) === 0xe0) return b - 256;            // negative fixint
      if ((b & 0xe0) === 0xa0) return readStr(b & 0x1f); // fixstr
      if ((b & 0xf0) === 0x90) return readArray(b & 0x0f); // fixarray
      if ((b & 0xf0) === 0x80) return readMap(b & 0x0f);   // fixmap
      if (b === 0xcc) return readU8();
      if (b === 0xcd) return readU16();
      if (b === 0xce) return readU32();
      if (b === 0xcf) { const hi = readU32(); const lo = readU32(); return hi * 2**32 + lo; }
      if (b === 0xd0) return readI8();
      if (b === 0xd1) return readI16();
      if (b === 0xd2) return readI32();
      if (b === 0xd3) { const hi = readI32(); const lo = readU32(); return hi * 2**32 + lo; }
      if (b === 0xca) return readF32();
      if (b === 0xcb) return readF64();
      if (b === 0xd9) return readStr(readU8());
      if (b === 0xda) return readStr(readU16());
      if (b === 0xdb) return readStr(readU32());
      if (b === 0xc4) { const n = readU8();  return readBytes(n); }
      if (b === 0xc5) { const n = readU16(); return readBytes(n); }
      if (b === 0xc6) { const n = readU32(); return readBytes(n); }
      if (b === 0xdc) return readArray(readU16());
      if (b === 0xdd) return readArray(readU32());
      if (b === 0xde) return readMap(readU16());
      if (b === 0xdf) return readMap(readU32());
      // ext types: skip
      if (b === 0xd4) { pos += 2; return null; }
      if (b === 0xd5) { pos += 3; return null; }
      if (b === 0xd6) { pos += 5; return null; }
      if (b === 0xd7) { pos += 9; return null; }
      if (b === 0xd8) { pos += 17; return null; }
      if (b === 0xc7) { const n = readU8(); pos += 1 + n; return null; }
      if (b === 0xc8) { const n = readU16(); pos += 1 + n; return null; }
      if (b === 0xc9) { const n = readU32(); pos += 1 + n; return null; }
      throw new Error(`Unknown msgpack byte 0x${b.toString(16)} at pos ${pos - 1}`);
    }

    return readValue();
  }

  return { encode, decode };
})();
