/**
 * Pixel dimensions for an embedded texture image, read straight from its
 * compressed-format header bytes - no image decode, no `<canvas>`, no
 * three.js. Model-viewer's public Scene-graph API (`Texture`/`Image`) never
 * exposes width/height, only a name/uri/thumbnail, so this is the only way
 * to show "this texture is 2048x2048" in the material inspector.
 *
 * Supports the formats PotionUI's own generators actually embed
 * (WebP - all three sub-formats: lossy VP8, lossless VP8L, extended VP8X -
 * and PNG) plus baseline JPEG for robustness against a plugin or a hand-authored
 * asset that embeds something else.
 */

export interface ImageDimensions {
	width: number;
	height: number;
}

export function decodeImageDimensions(bytes: Uint8Array, mimeType: string): ImageDimensions | null {
	const type = mimeType.toLowerCase();
	if (type.includes('webp')) return decodeWebpDimensions(bytes);
	if (type.includes('png')) return decodePngDimensions(bytes);
	if (type.includes('jpeg') || type.includes('jpg')) return decodeJpegDimensions(bytes);
	return null;
}

function decodePngDimensions(bytes: Uint8Array): ImageDimensions | null {
	// 8-byte signature, then a 4-byte length + 4-byte 'IHDR' type, then the
	// IHDR payload's first 8 bytes are width/height (both big-endian u32).
	if (bytes.length < 24) return null;
	const isPng =
		bytes[0] === 0x89 &&
		bytes[1] === 0x50 &&
		bytes[2] === 0x4e &&
		bytes[3] === 0x47 &&
		bytes[4] === 0x0d &&
		bytes[5] === 0x0a &&
		bytes[6] === 0x1a &&
		bytes[7] === 0x0a;
	if (!isPng) return null;
	const isIhdr = bytes[12] === 0x49 && bytes[13] === 0x48 && bytes[14] === 0x44 && bytes[15] === 0x52;
	if (!isIhdr) return null;

	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
	const width = view.getUint32(16, false);
	const height = view.getUint32(20, false);
	return width > 0 && height > 0 ? { width, height } : null;
}

function decodeWebpDimensions(bytes: Uint8Array): ImageDimensions | null {
	// RIFF container: 'RIFF' + u32 size (LE) + 'WEBP', then the first chunk's
	// FourCC tells us which of the three sub-formats follows. 25 is the
	// shortest a header carrying a dimension can be (VP8L: 20-byte prefix +
	// 1 signature byte + 4-byte packed dimensions).
	if (bytes.length < 25) return null;
	const isRiff = bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46;
	const isWebp = bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50;
	if (!isRiff || !isWebp) return null;

	const fourCc = String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15]);
	const chunkData = 20; // 12 (RIFF/WEBP) + 4 (chunk FourCC) + 4 (chunk size)
	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

	if (fourCc === 'VP8X') {
		// Extended format: 1 flags byte + 3 reserved, then two 24-bit
		// little-endian "dimension minus one" fields.
		const w = read24LE(bytes, chunkData + 4) + 1;
		const h = read24LE(bytes, chunkData + 7) + 1;
		return { width: w, height: h };
	}
	if (fourCc === 'VP8L') {
		// Lossless: 1 signature byte (0x2f), then a packed 32-bit LE value -
		// low 14 bits width-1, next 14 bits height-1.
		if (bytes[chunkData] !== 0x2f) return null;
		const packed = view.getUint32(chunkData + 1, true);
		const w = (packed & 0x3fff) + 1;
		const h = ((packed >> 14) & 0x3fff) + 1;
		return { width: w, height: h };
	}
	if (fourCc === 'VP8 ') {
		// Lossy: 3-byte frame tag, then a 3-byte start code (0x9d 0x01 0x2a),
		// then two 16-bit LE values whose low 14 bits are the dimension (the
		// top 2 bits are an unrelated scale hint).
		const startCode = chunkData + 3;
		if (bytes[startCode] !== 0x9d || bytes[startCode + 1] !== 0x01 || bytes[startCode + 2] !== 0x2a) {
			return null;
		}
		const w = view.getUint16(startCode + 3, true) & 0x3fff;
		const h = view.getUint16(startCode + 5, true) & 0x3fff;
		return { width: w, height: h };
	}
	return null;
}

function decodeJpegDimensions(bytes: Uint8Array): ImageDimensions | null {
	if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
	const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
	let offset = 2;
	while (offset + 9 < bytes.length) {
		if (bytes[offset] !== 0xff) {
			offset++;
			continue;
		}
		const marker = bytes[offset + 1];
		// SOFn markers carry the frame dimensions; C4/C8/CC are DHT/JPG/DAC,
		// not start-of-frame, despite falling in the 0xC0-0xCF range.
		const isSof = marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
		if (isSof) {
			const height = view.getUint16(offset + 5, false);
			const width = view.getUint16(offset + 7, false);
			return width > 0 && height > 0 ? { width, height } : null;
		}
		const segmentLength = view.getUint16(offset + 2, false);
		offset += 2 + segmentLength;
	}
	return null;
}

function read24LE(bytes: Uint8Array, offset: number): number {
	return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
}
