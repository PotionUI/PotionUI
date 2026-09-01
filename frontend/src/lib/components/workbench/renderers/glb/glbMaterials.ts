/**
 * Material/PBR-channel data for the mesh inspector, read from a parsed GLB
 * container (`glbContainer.ts`). Pure aside from the injectable blob-URL
 * factory: `createObjectUrl` defaults to `URL.createObjectURL` but every
 * call site (including tests, where jsdom has no `createObjectURL`) can pass
 * its own, so this module never assumes a browser.
 */
import type { GlbContainer, GlTFMaterial, GlTFTextureRef } from './glbContainer';
import { resolveEmbeddedImageBytes } from './glbContainer';
import { decodeImageDimensions } from './glbImageDimensions';

export type MaterialChannelKind =
	| 'baseColor'
	| 'metallicRoughness'
	| 'normal'
	| 'occlusion'
	| 'emissive';

export interface MaterialChannel {
	kind: MaterialChannelKind;
	/** Human label for the channel, e.g. "Metallic / Roughness". */
	label: string;
	/** Scalar/vector factor the shader multiplies the texture by (or the flat
	 * value used when there's no texture at all) - RGBA for baseColor and
	 * emissive, a single number for metallic/roughness/occlusion strength. */
	factor?: number[] | number;
	texture?: {
		width: number | null;
		height: number | null;
		mimeType: string;
		/** Object URL for an `<img>` swatch. Caller owns revoking it
		 * (`revokeMaterialChannelUrls`) once the inspector no longer needs it. */
		objectUrl: string;
	};
}

export interface MaterialInfo {
	index: number;
	name: string;
	doubleSided: boolean;
	alphaMode: 'OPAQUE' | 'MASK' | 'BLEND';
	channels: MaterialChannel[];
}

export interface ExtractMaterialsOptions {
	/** Defaults to `URL.createObjectURL`; overridable for tests/non-browser callers. */
	createObjectUrl?: (blob: Blob) => string;
}

function buildChannel(
	container: GlbContainer,
	kind: MaterialChannelKind,
	label: string,
	factor: number[] | number | undefined,
	textureRef: GlTFTextureRef | undefined,
	createObjectUrl: (blob: Blob) => string
): MaterialChannel {
	const channel: MaterialChannel = { kind, label, factor };
	if (!textureRef) return channel;

	const imageIndex = container.json.textures?.[textureRef.index]?.source;
	if (imageIndex == null) return channel;

	const resolved = resolveEmbeddedImageBytes(container, imageIndex);
	if (!resolved) return channel;

	const dimensions = decodeImageDimensions(resolved.bytes, resolved.mimeType);
	// `bytes` is a view into the container's shared BIN chunk ArrayBuffer.
	// `.slice()` copies it into its own freshly-allocated buffer (both so the
	// resulting object URL survives independently of that buffer's lifetime,
	// and because a `Uint8Array` view over someone else's `ArrayBufferLike`
	// isn't assignable to `BlobPart` under this project's TS/DOM lib types).
	const blob = new Blob([resolved.bytes.slice()], { type: resolved.mimeType });
	channel.texture = {
		width: dimensions?.width ?? null,
		height: dimensions?.height ?? null,
		mimeType: resolved.mimeType,
		objectUrl: createObjectUrl(blob)
	};
	return channel;
}

function extractOneMaterial(
	container: GlbContainer,
	material: GlTFMaterial,
	index: number,
	createObjectUrl: (blob: Blob) => string
): MaterialInfo {
	const pbr = material.pbrMetallicRoughness ?? {};
	const channels: MaterialChannel[] = [
		buildChannel(
			container,
			'baseColor',
			'Base color',
			pbr.baseColorFactor ?? [1, 1, 1, 1],
			pbr.baseColorTexture,
			createObjectUrl
		),
		buildChannel(
			container,
			'metallicRoughness',
			'Metallic / roughness',
			[pbr.metallicFactor ?? 1, pbr.roughnessFactor ?? 1],
			pbr.metallicRoughnessTexture,
			createObjectUrl
		)
	];
	if (material.normalTexture) {
		channels.push(buildChannel(container, 'normal', 'Normal', undefined, material.normalTexture, createObjectUrl));
	}
	if (material.occlusionTexture) {
		channels.push(
			buildChannel(container, 'occlusion', 'Occlusion', undefined, material.occlusionTexture, createObjectUrl)
		);
	}
	if (material.emissiveTexture || material.emissiveFactor) {
		channels.push(
			buildChannel(
				container,
				'emissive',
				'Emissive',
				material.emissiveFactor ?? [0, 0, 0],
				material.emissiveTexture,
				createObjectUrl
			)
		);
	}

	return {
		index,
		name: material.name || `Material ${index + 1}`,
		doubleSided: material.doubleSided ?? false,
		alphaMode: material.alphaMode ?? 'OPAQUE',
		channels
	};
}

export function extractMaterials(container: GlbContainer, options: ExtractMaterialsOptions = {}): MaterialInfo[] {
	const createObjectUrl = options.createObjectUrl ?? ((blob: Blob) => URL.createObjectURL(blob));
	return (container.json.materials ?? []).map((material, index) =>
		extractOneMaterial(container, material, index, createObjectUrl)
	);
}

/** Names for the GLB's `animations[]` array, falling back to a positional
 * label for an unnamed clip (glTF permits `name` to be absent). */
export function extractAnimationNames(container: GlbContainer): string[] {
	return (container.json.animations ?? []).map((anim, i) => anim.name || `Animation ${i + 1}`);
}

/** Revokes every texture swatch's object URL - call once the inspector data
 * for a given mesh is no longer needed (mesh switched, component destroyed). */
export function revokeMaterialChannelUrls(
	materials: MaterialInfo[],
	revokeObjectUrl: (url: string) => void = (url) => URL.revokeObjectURL(url)
): void {
	for (const material of materials) {
		for (const channel of material.channels) {
			if (channel.texture) revokeObjectUrl(channel.texture.objectUrl);
		}
	}
}
