/**
 * SHA-256 of a file computed in the browser, chunk by chunk.
 *
 * `crypto.subtle.digest` needs the whole input in memory at once and has no streaming
 * variant, so a 50 MB upload would be copied into a single buffer. This module implements
 * SHA-256 (FIPS 180-4) incrementally instead and feeds it fixed-size slices of the file,
 * keeping memory bounded without adding a dependency.
 */

/** Size of each slice read from the file. */
export const HASH_CHUNK_BYTES = 4 * 1024 * 1024;

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const INITIAL_STATE = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

const BLOCK_BYTES = 64;

function rotr(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}

/** Incremental SHA-256: call `update` with consecutive pieces, then `digestHex` once. */
export class Sha256 {
  private readonly state = new Uint32Array(INITIAL_STATE);
  private readonly words = new Uint32Array(64);
  private readonly pending = new Uint8Array(BLOCK_BYTES);
  private pendingLength = 0;
  private totalBytes = 0;
  private finished = false;

  update(data: Uint8Array): this {
    if (this.finished) {
      throw new Error("Sha256.update called after digestHex");
    }
    this.totalBytes += data.length;
    let offset = 0;
    if (this.pendingLength > 0) {
      const take = Math.min(BLOCK_BYTES - this.pendingLength, data.length);
      this.pending.set(data.subarray(0, take), this.pendingLength);
      this.pendingLength += take;
      offset = take;
      if (this.pendingLength < BLOCK_BYTES) {
        return this;
      }
      this.compress(this.pending, 0);
      this.pendingLength = 0;
    }
    for (; offset + BLOCK_BYTES <= data.length; offset += BLOCK_BYTES) {
      this.compress(data, offset);
    }
    this.pending.set(data.subarray(offset), 0);
    this.pendingLength = data.length - offset;
    return this;
  }

  /** Lowercase hexadecimal digest; the instance cannot be updated afterwards. */
  digestHex(): string {
    if (this.finished) {
      throw new Error("Sha256.digestHex called twice");
    }
    const bitLength = this.totalBytes * 8;
    const padLength = this.pendingLength < 56 ? 56 - this.pendingLength : 120 - this.pendingLength;
    const tail = new Uint8Array(padLength + 8);
    tail[0] = 0x80;
    const view = new DataView(tail.buffer);
    view.setUint32(padLength, Math.floor(bitLength / 0x100000000));
    view.setUint32(padLength + 4, bitLength >>> 0);
    this.update(tail);
    this.finished = true;
    return Array.from(this.state, (word) => word.toString(16).padStart(8, "0")).join("");
  }

  private compress(block: Uint8Array, offset: number): void {
    const w = this.words;
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4;
      w[i] =
        ((block[j] ?? 0) << 24) |
        ((block[j + 1] ?? 0) << 16) |
        ((block[j + 2] ?? 0) << 8) |
        (block[j + 3] ?? 0);
    }
    for (let i = 16; i < 64; i++) {
      const w15 = w[i - 15] ?? 0;
      const w2 = w[i - 2] ?? 0;
      const s0 = rotr(w15, 7) ^ rotr(w15, 18) ^ (w15 >>> 3);
      const s1 = rotr(w2, 17) ^ rotr(w2, 19) ^ (w2 >>> 10);
      w[i] = ((w[i - 16] ?? 0) + s0 + (w[i - 7] ?? 0) + s1) | 0;
    }
    const s = this.state;
    let a = s[0] ?? 0;
    let b = s[1] ?? 0;
    let c = s[2] ?? 0;
    let d = s[3] ?? 0;
    let e = s[4] ?? 0;
    let f = s[5] ?? 0;
    let g = s[6] ?? 0;
    let h = s[7] ?? 0;
    for (let i = 0; i < 64; i++) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const choice = (e & f) ^ (~e & g);
      const t1 = (h + s1 + choice + (K[i] ?? 0) + (w[i] ?? 0)) | 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + majority) | 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) | 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) | 0;
    }
    s[0] = (s[0] ?? 0) + a;
    s[1] = (s[1] ?? 0) + b;
    s[2] = (s[2] ?? 0) + c;
    s[3] = (s[3] ?? 0) + d;
    s[4] = (s[4] ?? 0) + e;
    s[5] = (s[5] ?? 0) + f;
    s[6] = (s[6] ?? 0) + g;
    s[7] = (s[7] ?? 0) + h;
  }
}

/** SHA-256 of a byte array as lowercase hex. */
export function sha256Hex(data: Uint8Array): string {
  return new Sha256().update(data).digestHex();
}

function readChunk(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === "function") {
    return blob.arrayBuffer();
  }
  // Older engines (and jsdom) may lack Blob.arrayBuffer.
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsArrayBuffer(blob);
  });
}

/**
 * SHA-256 of `file` as lowercase hex, reading `chunkBytes` at a time.
 *
 * `onProgress` receives the fraction hashed so far (0 to 1) after each chunk.
 */
export async function hashFile(
  file: Blob,
  options: { chunkBytes?: number; onProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<string> {
  const chunkBytes = options.chunkBytes ?? HASH_CHUNK_BYTES;
  const hash = new Sha256();
  for (let start = 0; start < file.size; start += chunkBytes) {
    options.signal?.throwIfAborted();
    const chunk = await readChunk(file.slice(start, start + chunkBytes));
    hash.update(new Uint8Array(chunk));
    options.onProgress?.(Math.min(1, (start + chunkBytes) / file.size));
  }
  return hash.digestHex();
}
