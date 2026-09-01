import { describe, it, expect } from 'vitest';
import { parseGlbContainer, resolveEmbeddedImageBytes, type GlTFDocument } from './glbContainer';

/** Builds a minimal-but-real binary glTF buffer: 12-byte header, a JSON
 * chunk (space-padded to 4 bytes), and an optional BIN chunk (zero-padded). */
function buildGlb(json: GlTFDocument, binBytes?: Uint8Array): ArrayBuffer {
	const jsonText = JSON.stringify(json);
	const jsonBytes = new TextEncoder().encode(jsonText);
	const jsonPadded = padTo4(jsonBytes, 0x20);
	const binPadded = binBytes ? padTo4(binBytes, 0x00) : null;

	const totalLength =
		12 + 8 + jsonPadded.length + (binPadded ? 8 + binPadded.length : 0);
	const buffer = new ArrayBuffer(totalLength);
	const view = new DataView(buffer);
	const bytes = new Uint8Array(buffer);

	view.setUint32(0, 0x46546c67, true); // magic 'glTF'
	view.setUint32(4, 2, true); // version
	view.setUint32(8, totalLength, true);

	let offset = 12;
	view.setUint32(offset, jsonPadded.length, true);
	view.setUint32(offset + 4, 0x4e4f534a, true); // 'JSON'
	bytes.set(jsonPadded, offset + 8);
	offset += 8 + jsonPadded.length;

	if (binPadded) {
		view.setUint32(offset, binPadded.length, true);
		view.setUint32(offset + 4, 0x004e4942, true); // 'BIN\0'
		bytes.set(binPadded, offset + 8);
	}

	return buffer;
}

function padTo4(bytes: Uint8Array, fill: number): Uint8Array {
	const remainder = bytes.length % 4;
	if (remainder === 0) return bytes;
	const padded = new Uint8Array(bytes.length + (4 - remainder));
	padded.set(bytes);
	padded.fill(fill, bytes.length);
	return padded;
}

describe('parseGlbContainer', () => {
	it('parses the JSON chunk of a JSON-only container', () => {
		const buffer = buildGlb({ materials: [{ name: 'Plain' }] });
		const container = parseGlbContainer(buffer);
		expect(container?.json.materials?.[0].name).toBe('Plain');
		expect(container?.binChunk).toBeNull();
	});

	it('parses both the JSON and BIN chunks', () => {
		const bin = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
		const buffer = buildGlb({ buffers: [{ byteLength: bin.length }] }, bin);
		const container = parseGlbContainer(buffer);
		expect(container?.binChunk).not.toBeNull();
		expect(new Uint8Array(container!.binChunk!)).toEqual(bin);
	});

	it('returns null for the wrong magic number', () => {
		const buffer = new ArrayBuffer(20);
		new DataView(buffer).setUint32(0, 0xdeadbeef, true);
		expect(parseGlbContainer(buffer)).toBeNull();
	});

	it('returns null for a buffer shorter than the header', () => {
		expect(parseGlbContainer(new ArrayBuffer(4))).toBeNull();
	});

	it('returns null when totalLength in the header exceeds the actual buffer', () => {
		const buffer = new ArrayBuffer(20);
		const view = new DataView(buffer);
		view.setUint32(0, 0x46546c67, true);
		view.setUint32(4, 2, true);
		view.setUint32(8, 999999, true);
		expect(parseGlbContainer(buffer)).toBeNull();
	});

	it('returns null when the JSON chunk is malformed', () => {
		const buffer = new ArrayBuffer(24);
		const view = new DataView(buffer);
		const bytes = new Uint8Array(buffer);
		view.setUint32(0, 0x46546c67, true);
		view.setUint32(4, 2, true);
		view.setUint32(8, 24, true);
		view.setUint32(12, 4, true);
		view.setUint32(16, 0x4e4f534a, true);
		bytes.set(new TextEncoder().encode('{bad'), 20);
		expect(parseGlbContainer(buffer)).toBeNull();
	});
});

describe('resolveEmbeddedImageBytes', () => {
	it('slices the bufferView range out of the BIN chunk', () => {
		const bin = new Uint8Array([9, 9, 9, 9, 0xaa, 0xbb, 0xcc, 0xdd, 7, 7, 7, 7]);
		const json: GlTFDocument = {
			images: [{ bufferView: 0, mimeType: 'image/png' }],
			bufferViews: [{ buffer: 0, byteOffset: 4, byteLength: 4 }],
			buffers: [{ byteLength: bin.length }]
		};
		const container = parseGlbContainer(buildGlb(json, bin));
		const resolved = resolveEmbeddedImageBytes(container!, 0);
		expect(resolved?.mimeType).toBe('image/png');
		expect(Array.from(resolved!.bytes)).toEqual([0xaa, 0xbb, 0xcc, 0xdd]);
	});

	it('returns null for an external (uri-based) image', () => {
		const container = parseGlbContainer(buildGlb({ images: [{ uri: 'texture.png' }] }));
		expect(resolveEmbeddedImageBytes(container!, 0)).toBeNull();
	});

	it('returns null when there is no BIN chunk to resolve against', () => {
		const container = parseGlbContainer(
			buildGlb({
				images: [{ bufferView: 0, mimeType: 'image/png' }],
				bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 4 }]
			})
		);
		expect(resolveEmbeddedImageBytes(container!, 0)).toBeNull();
	});

	it('returns null for an out-of-range image index', () => {
		const container = parseGlbContainer(buildGlb({}));
		expect(resolveEmbeddedImageBytes(container!, 3)).toBeNull();
	});
});
