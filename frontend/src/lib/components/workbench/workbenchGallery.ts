/**
 * The workbench's gallery chain and per-file-type action set.
 *
 * A generation's outputs are addressed by ONE absolute index across four
 * ordered buckets - images, then videos, then audios, then meshes. That order
 * is the contract between the prev/next arrows, the gallery strip, the
 * parameter lookup and the restore-from-history path; when a call site
 * re-derives it and forgets a bucket (meshes and audios both got forgotten),
 * the tail of the chain becomes unreachable.
 *
 * `kind` comes from the BUCKET, not from `item.file_type`: the history restore
 * path (mapGenerationFiles) only stamps `file_type` on audios and meshes, so an
 * image's and a video's bucket is the only thing that distinguishes them.
 */
import type { ImageData, VideoData, MeshData } from '$lib/types/tabs';
import type { AudioData } from '$lib/types/audio';
import { normalizeFileType, type MediaFileType } from '$lib/utils/fileType';

export interface WorkbenchBatches {
	images?: ImageData[] | null;
	videos?: VideoData[] | null;
	audios?: AudioData[] | null;
	meshes?: MeshData[] | null;
}

export interface GalleryEntry {
	item: any;
	kind: MediaFileType;
	/** Absolute index in the chain - the same number the workbench holds. */
	index: number;
}

/** Bucket order. Any code walking the chain must use this. */
const BUCKETS: Array<{ key: keyof WorkbenchBatches; kind: MediaFileType }> = [
	{ key: 'images', kind: 'image' },
	{ key: 'videos', kind: 'video' },
	{ key: 'audios', kind: 'audio' },
	{ key: 'meshes', kind: 'mesh' }
];

function bucket(batches: WorkbenchBatches, key: keyof WorkbenchBatches): any[] {
	const list = batches?.[key];
	return Array.isArray(list) ? list : [];
}

/** Total number of addressable gallery items across all four buckets. */
export function galleryTotal(batches: WorkbenchBatches): number {
	return BUCKETS.reduce((sum, b) => sum + bucket(batches, b.key).length, 0);
}

/** The entry at an absolute gallery index, or null when out of range. */
export function galleryItemAt(batches: WorkbenchBatches, index: number): GalleryEntry | null {
	if (!Number.isInteger(index) || index < 0) return null;

	let remaining = index;
	for (const b of BUCKETS) {
		const list = bucket(batches, b.key);
		if (remaining < list.length) {
			const item = list[remaining];
			return item ? { item, kind: b.kind, index } : null;
		}
		remaining -= list.length;
	}
	return null;
}

/** First entry in the chain, or null when the generation produced nothing. */
export function firstGalleryEntry(batches: WorkbenchBatches): GalleryEntry | null {
	return galleryItemAt(batches, 0);
}

/** Full-size URL for a gallery item (the display URL is only a fallback). */
export function galleryItemUrl(item: any): string | null {
	if (!item || typeof item !== 'object') return null;
	const url = item.originalUrl || item.url;
	return typeof url === 'string' && url ? url : null;
}

/**
 * The file type to render an entry as. An explicit `file_type` on the item
 * wins (normalized - it can arrive uppercase from the history API), otherwise
 * the bucket it came out of decides.
 */
export function entryFileType(entry: GalleryEntry | null): MediaFileType | '' {
	if (!entry) return '';
	const declared = normalizeFileType(entry.item?.file_type);
	if (declared) return declared as MediaFileType;
	return entry.kind;
}

export interface WorkbenchActions {
	/** A "save this file" affordance makes sense (audio carries its own). */
	canDownload: boolean;
	canOpenInNewTab: boolean;
	/** The expand-to-fullscreen modal only knows how to draw <img>/<video>. */
	canExpand: boolean;
	/** Side-by-side comparison against another generation. */
	canCompare: boolean;
	canZoom: boolean;
	canCopyImage: boolean;
	/** Width/height/size chrome, and the metadata probe that fills it. */
	hasPixelMetadata: boolean;
}

/**
 * Which of the workbench's per-output actions apply to a file type. Anything
 * not in the built-in set (whatever a plugin registers next) degrades to
 * download + open-in-new-tab rather than silently offering an image-only
 * tool that does nothing.
 */
export function workbenchActionsFor(fileType: unknown): WorkbenchActions {
	const kind = normalizeFileType(fileType);
	const isImage = kind === 'image' || kind === '';
	const isVideo = kind === 'video';
	const isAudio = kind === 'audio';
	const isMesh = kind === 'mesh';

	return {
		canDownload: !isAudio,
		canOpenInNewTab: true,
		// A mesh's fullscreen expand renders through MeshPreview (its own
		// interactive viewer), not the modal's <img>/<video> pair - see the
		// mesh branch of Workbench.svelte's "Image/Video Preview Modal".
		canExpand: isImage || isVideo || isMesh,
		canCompare: isImage || isVideo,
		canZoom: isImage,
		canCopyImage: isImage,
		hasPixelMetadata: isImage || isVideo
	};
}

const DEFAULT_EXTENSIONS: Record<string, string> = {
	image: 'png',
	video: 'mp4',
	audio: 'wav',
	mesh: 'glb'
};

/**
 * Download extension for an output. The URL's own extension is authoritative
 * when it has one (a .webm video, a .obj mesh); the per-type default is only a
 * fallback for blob:/data: URLs and paths without a suffix.
 */
export function downloadExtensionFor(fileType: unknown, item?: any): string {
	const kind = normalizeFileType(fileType);
	const url = galleryItemUrl(item) || '';
	const basename = url.split(/[?#]/)[0].split('/').pop() || '';
	if (basename.includes('.') && !url.startsWith('data:')) {
		const ext = basename.split('.').pop() || '';
		if (ext && /^[a-z0-9]{1,5}$/i.test(ext)) return ext.toLowerCase();
	}
	if (kind === 'mesh') {
		const format = typeof item?.mesh_format === 'string' ? item.mesh_format : '';
		if (format) return format.toLowerCase();
	}
	return DEFAULT_EXTENSIONS[kind] || 'png';
}
