// Zero-dependency PWA icon generator.
//
// It renders the same deep-blue rounded-square + white gas-pump mark that is
// used as the inline favicon, and writes PNG files by hand using node:zlib.
// The output is deterministic: re-running this script produces byte-identical
// files (same zlib implementation, no timestamps, no randomness).
import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const outDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'icons');

const BLUE = [26, 115, 232, 255]; // #1a73e8
const WHITE = [255, 255, 255, 255];

// ── PNG encoding helpers ──────────────────────────────────────────────────

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const typeBytes = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 0);
  return Buffer.concat([length, typeBytes, data, crc]);
}

function ihdr(width, height) {
  const data = Buffer.alloc(13);
  data.writeUInt32BE(width, 0);
  data.writeUInt32BE(height, 4);
  data[8] = 8; // bit depth
  data[9] = 6; // color type: RGBA
  data[10] = 0; // compression
  data[11] = 0; // filter
  data[12] = 0; // interlace
  return chunk('IHDR', data);
}

function encodePNG(width, height, pixels) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0; // filter type: None
    pixels.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const idat = deflateSync(raw, { level: 9 });
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    ihdr(width, height),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0))
  ]);
}

// ── Tiny deterministic rasterizer ─────────────────────────────────────────

function setPx(buf, width, x, y, color) {
  if (x < 0 || y < 0 || x >= width || y >= width) return;
  const i = (y * width + x) * 4;
  buf[i] = color[0];
  buf[i + 1] = color[1];
  buf[i + 2] = color[2];
  buf[i + 3] = color[3];
}

function inRoundedRect(x, y, left, top, right, bottom, radius) {
  if (x < left || x > right || y < top || y > bottom) return false;
  const cx = Math.max(left + radius, Math.min(x, right - radius));
  const cy = Math.max(top + radius, Math.min(y, bottom - radius));
  const dx = x - cx;
  const dy = y - cy;
  return dx * dx + dy * dy <= radius * radius;
}

function fillRect(buf, width, height, x0, y0, x1, y1, color) {
  const X0 = Math.max(0, Math.floor(x0));
  const X1 = Math.min(width, Math.ceil(x1));
  const Y0 = Math.max(0, Math.floor(y0));
  const Y1 = Math.min(height, Math.ceil(y1));
  for (let y = Y0; y < Y1; y++) {
    for (let x = X0; x < X1; x++) {
      setPx(buf, width, x, y, color);
    }
  }
}

function fillRoundedRect(buf, width, height, x0, y0, x1, y1, radius, color) {
  const X0 = Math.max(0, Math.floor(x0));
  const X1 = Math.min(width, Math.ceil(x1));
  const Y0 = Math.max(0, Math.floor(y0));
  const Y1 = Math.min(height, Math.ceil(y1));
  for (let y = Y0; y < Y1; y++) {
    for (let x = X0; x < X1; x++) {
      if (inRoundedRect(x + 0.5, y + 0.5, x0, y0, x1, y1, radius)) {
        setPx(buf, width, x, y, color);
      }
    }
  }
}

// ── Gas pump mark (32×32 viewBox, matching the inline favicon) ────────────

function drawPump(buf, width, height, offset, scale) {
  const X = (v) => offset + v * scale;
  const Y = (v) => offset + v * scale;
  const R = (v) => v * scale;

  // Top fuel-canister neck
  fillRoundedRect(buf, width, height, X(9), Y(5.5), X(17), Y(12), R(2), WHITE);
  // Main pump body
  fillRoundedRect(buf, width, height, X(7.5), Y(12), X(18.5), Y(27.5), R(2.5), WHITE);
  // Blue price screen
  fillRoundedRect(buf, width, height, X(7.5), Y(15.5), X(13.5), Y(19.5), R(1), BLUE);
  // Hose + nozzle on the right
  fillRoundedRect(buf, width, height, X(21), Y(15.5), X(25.5), Y(23), R(2), WHITE);
  fillRect(buf, width, height, X(22.5), Y(23), X(24), Y(25.5), WHITE);
}

function renderIcon(size, { rounded = false, inset = 0 } = {}) {
  const buf = Buffer.alloc(size * size * 4);

  if (rounded) {
    fillRoundedRect(buf, size, size, 0, 0, size, size, size * (7 / 32), BLUE);
  } else {
    fillRect(buf, size, size, 0, 0, size, size, BLUE);
  }

  const padding = inset * size;
  const scale = (size - padding * 2) / 32;
  const offset = (size - scale * 32) / 2;
  drawPump(buf, size, size, offset, scale);
  return buf;
}

function writeIcon(name, size, opts) {
  const png = encodePNG(size, size, renderIcon(size, opts));
  writeFileSync(join(outDir, name), png);
  console.log(`wrote ${name} (${png.length} bytes)`);
}

mkdirSync(outDir, { recursive: true });
writeIcon('icon-192.png', 192, { rounded: true });
writeIcon('icon-512.png', 512, { rounded: true });
writeIcon('maskable-512.png', 512, { inset: 0.1 });
writeIcon('apple-touch-icon-180.png', 180, { inset: 0.1 });
