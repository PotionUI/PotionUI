/**
 * Binary glTF (.glb) container parsing - the 12-byte header plus a JSON chunk
 * and an optional binary (BIN) chunk, per the glTF 2.0 binary spec
 * (https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#binary-gltf-layout).
 * Deliberately three.js-free: the material inspector only needs the JSON
 * document's material/texture/image/bufferView graph plus raw texture bytes,
 * not a rendered scene, so this reads the container directly rather than
 * going through model-viewer's (symbol-gated) scene-graph facade.
 */

const GLB_MAGIC = 0x46546c67; // 'glTF', little-endian
const CHUNK_TYPE_JSON = 0x4e4f534a; // 'JSON'
const CHUNK_TYPE_BIN = 0x004e4942; // 'BIN\0'

/** Minimal shape of the glTF JSON chunk this module reads. Fields this
 * codebase never touches (accessors, meshes, skins, ...) are left untyped. */
export interface GlTFDocument {
	materials?: GlTFMaterial[];
	textures?: Array<{ source?: number; sampler?: number }>;
	images?: Array<{ uri?: string; mimeType?: string; bufferView?: number }>;
	bufferViews?: Array<{ buffer: number; byteOffset?: number; byteLength: number }>;
	buffers?: Array<{ byteLength: number; uri?: string }>;
	animations?: Array<{ name?: string }>;
	[key: string]: unknown;
}

export interface GlTFTextureRef {
	index: number;
	texCoord?: number;
}

export interface GlTFMaterial {
	name?: string;
	doubleSided?: boolean;
	alphaMode?: 'OPAQUE' | 'MASK' | 'BLEND';
	alphaCutoff?: number;
	emissiveFactor?: [number, number, number];
	emissiveTexture?: GlTFTextureRef;
	normalTexture?: GlTFTextureRef;
	occlusionTexture?: GlTFTextureRef;
	pbrMetallicRoughness?: {
		baseColorFactor?: [number, number, number, number];
		baseColorTexture?: GlTFTextureRef;
		metallicFactor?: number;
		roughnessFactor?: number;
		metallicRoughnessTexture?: GlTFTextureRef;
	};
	[key: string]: unknown;
}

export interface GlbContainer {
	json: GlTFDocument;
	/** The single embedded binary buffer (buffer index 0), or null when the
	 * GLB carries no BIN chunk (a JSON-only container with external/data-uri
	 * images only - unusual but not invalid). */
	binChunk: ArrayBuffer | null;
}

/**
 * Parses a .glb ArrayBuffer into its JSON document and binary chunk.
 * Returns null for anything that isn't a valid binary-glTF container
 * (wrong magic, truncated header, or a non-JSON first chunk) - callers treat
 * that as "no inspector data available" rather than an error, since a mesh
 * can legitimately be .obj/.ply/.gltf instead of .glb.
 */
export function parseGlbContainer(buffer: ArrayBuffer): GlbContainer | null {
	if (buffer.byteLength < 12) return null;

	const view = new DataView(buffer);
	const magic = view.getUint32(0, true);
	if (magic !== GLB_MAGIC) return null;
	// version at offset 4 - unused; the binary chunk layout has been stable
	// since glTF 2.0's introduction and this module doesn't special-case it.
	const totalLength = view.getUint32(8, true);
	if (totalLength > buffer.byteLength) return null;

	let offset = 12;
	let json: GlTFDocument | null = null;
	let binChunk: ArrayBuffer | null = null;

	while (offset + 8 <= totalLength) {
		const chunkLength = view.getUint32(offset, true);
		const chunkType = view.getUint32(offset + 4, true);
		const chunkStart = offset + 8;
		const chunkEnd = chunkStart + chunkLength;
		if (chunkEnd > totalLength) break;

		if (chunkType === CHUNK_TYPE_JSON && !json) {
			const text = new TextDecoder('utf-8').decode(buffer.slice(chunkStart, chunkEnd));
			try {
				json = JSON.parse(text) as GlTFDocument;
			} catch {
				return null;
			}
		} else if (chunkType === CHUNK_TYPE_BIN && !binChunk) {
			binChunk = buffer.slice(chunkStart, chunkEnd);
		}

		offset = chunkEnd;
	}

	if (!json) return null;
	return { json, binChunk };
}

/**
 * Raw bytes for a glTF `images[]` entry that's embedded via `bufferView`
 * (the shape every core-generated GLB uses - TRELLIS ships WebP-embedded
 * textures, never external URIs). Returns null for an external/data-uri
 * image (`uri` set instead of `bufferView`) or a bufferView this container's
 * single BIN chunk can't resolve.
 */
export function resolveEmbeddedImageBytes(
	container: GlbContainer,
	imageIndex: number
): { bytes: Uint8Array; mimeType: string } | null {
	const image = container.json.images?.[imageIndex];
	if (!image || image.bufferView == null || !container.binChunk) return null;

	const bufferView = container.json.bufferViews?.[image.bufferView];
	if (!bufferView) return null;

	const byteOffset = bufferView.byteOffset ?? 0;
	const byteEnd = byteOffset + bufferView.byteLength;
	if (byteEnd > container.binChunk.byteLength) return null;

	const bytes = new Uint8Array(container.binChunk, byteOffset, bufferView.byteLength);
	const mimeType = image.mimeType || 'application/octet-stream';
	return { bytes, mimeType };
}
