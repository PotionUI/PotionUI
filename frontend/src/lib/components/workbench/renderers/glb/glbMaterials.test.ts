import { describe, it, expect } from 'vitest';
import { parseGlbContainer, type GlTFDocument } from './glbContainer';
import { extractMaterials, extractAnimationNames, revokeMaterialChannelUrls } from './glbMaterials';

function buildGlb(json: GlTFDocument, binBytes?: Uint8Array): ArrayBuffer {
	const jsonBytes = padTo4(new TextEncoder().encode(JSON.stringify(json)), 0x20);
	const binPadded = binBytes ? padTo4(binBytes, 0x00) : null;
	const totalLength = 12 + 8 + jsonBytes.length + (binPadded ? 8 + binPadded.length : 0);
	const buffer = new ArrayBuffer(totalLength);
	const view = new DataView(buffer);
	const bytes = new Uint8Array(buffer);

	view.setUint32(0, 0x46546c67, true);
	view.setUint32(4, 2, true);
	view.setUint32(8, totalLength, true);

	let offset = 12;
	view.setUint32(offset, jsonBytes.length, true);
	view.setUint32(offset + 4, 0x4e4f534a, true);
	bytes.set(jsonBytes, offset + 8);
	offset += 8 + jsonBytes.length;

	if (binPadded) {
		view.setUint32(offset, binPadded.length, true);
		view.setUint32(offset + 4, 0x004e4942, true);
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

/** A real (24-byte) PNG IHDR header carrying the given dimensions - enough
 * for `decodeImageDimensions` to read, not a decodable image. */
function fakePng(width: number, height: number): Uint8Array {
	const bytes = new Uint8Array(24);
	const view = new DataView(bytes.buffer);
	bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
	view.setUint32(8, 13, false);
	bytes.set([0x49, 0x48, 0x44, 0x52], 12);
	view.setUint32(16, width, false);
	view.setUint32(20, height, false);
	return bytes;
}

// TRELLIS-shaped fixture: one material with an RGBA base-color texture and a
// metallic(B)/roughness(G) texture, doubleSided - matching the brief's
// description of this codebase's own GLB output.
function buildTrellisFixture() {
	const baseColorPng = fakePng(64, 64);
	const metallicRoughnessPng = fakePng(32, 32);
	const bin = new Uint8Array(baseColorPng.length + metallicRoughnessPng.length);
	bin.set(baseColorPng, 0);
	bin.set(metallicRoughnessPng, baseColorPng.length);

	const json: GlTFDocument = {
		materials: [
			{
				name: 'trellis_material',
				doubleSided: true,
				alphaMode: 'BLEND',
				pbrMetallicRoughness: {
					baseColorFactor: [1, 1, 1, 1],
					baseColorTexture: { index: 0 },
					metallicFactor: 1,
					roughnessFactor: 1,
					metallicRoughnessTexture: { index: 1 }
				}
			}
		],
		textures: [{ source: 0 }, { source: 1 }],
		images: [
			{ bufferView: 0, mimeType: 'image/png' },
			{ bufferView: 1, mimeType: 'image/png' }
		],
		bufferViews: [
			{ buffer: 0, byteOffset: 0, byteLength: baseColorPng.length },
			{ buffer: 0, byteOffset: baseColorPng.length, byteLength: metallicRoughnessPng.length }
		],
		buffers: [{ byteLength: bin.length }],
		animations: [{ name: 'Idle' }, {}]
	};

	return parseGlbContainer(buildGlb(json, bin))!;
}

describe('extractMaterials', () => {
	it('reads doubleSided/alphaMode and both texture channels with their resolutions', () => {
		const container = buildTrellisFixture();
		const urls: string[] = [];
		const materials = extractMaterials(container, {
			createObjectUrl: (blob) => {
				const url = `blob:fake-${urls.length}:${blob.size}`;
				urls.push(url);
				return url;
			}
		});

		expect(materials).toHaveLength(1);
		const [material] = materials;
		expect(material.name).toBe('trellis_material');
		expect(material.doubleSided).toBe(true);
		expect(material.alphaMode).toBe('BLEND');

		const baseColor = material.channels.find((c) => c.kind === 'baseColor');
		expect(baseColor?.factor).toEqual([1, 1, 1, 1]);
		expect(baseColor?.texture).toMatchObject({ width: 64, height: 64, mimeType: 'image/png' });

		const metallicRoughness = material.channels.find((c) => c.kind === 'metallicRoughness');
		expect(metallicRoughness?.factor).toEqual([1, 1]);
		expect(metallicRoughness?.texture).toMatchObject({ width: 32, height: 32, mimeType: 'image/png' });

		expect(urls).toHaveLength(2);
	});

	it('falls back to a positional name when the material has none', () => {
		const container = parseGlbContainer(buildGlb({ materials: [{}] }))!;
		const [material] = extractMaterials(container);
		expect(material.name).toBe('Material 1');
		expect(material.doubleSided).toBe(false);
		expect(material.alphaMode).toBe('OPAQUE');
	});

	it('omits the texture field for a factor-only channel', () => {
		const container = parseGlbContainer(
			buildGlb({ materials: [{ pbrMetallicRoughness: { baseColorFactor: [0.2, 0.4, 0.6, 1] } }] })
		)!;
		const [material] = extractMaterials(container);
		const baseColor = material.channels.find((c) => c.kind === 'baseColor');
		expect(baseColor?.texture).toBeUndefined();
		expect(baseColor?.factor).toEqual([0.2, 0.4, 0.6, 1]);
	});

	it('returns an empty list for a GLB with no materials', () => {
		const container = parseGlbContainer(buildGlb({}))!;
		expect(extractMaterials(container)).toEqual([]);
	});
});

describe('extractAnimationNames', () => {
	it('names an unnamed clip positionally', () => {
		const container = buildTrellisFixture();
		expect(extractAnimationNames(container)).toEqual(['Idle', 'Animation 2']);
	});

	it('returns an empty list when the GLB has no animations', () => {
		const container = parseGlbContainer(buildGlb({}))!;
		expect(extractAnimationNames(container)).toEqual([]);
	});
});

describe('revokeMaterialChannelUrls', () => {
	it('revokes exactly the object URLs it created', () => {
		const container = buildTrellisFixture();
		const materials = extractMaterials(container, { createObjectUrl: (b) => `blob:${b.size}` });
		const revoked: string[] = [];
		revokeMaterialChannelUrls(materials, (url) => revoked.push(url));
		expect(revoked.sort()).toEqual(['blob:24', 'blob:24'].sort());
	});
});
