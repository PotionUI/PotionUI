// Per-generation cache of the latest `gallery_update` payload, bridging it to
// the later `generation_complete`/`generation_error` for the SAME generation
// id. Needed because those terminal messages carry no output of their own
// (the backend's `generation_complete` broadcast is `status.model_dump()`,
// never the images/videos/audios themselves -- see
// `_handle_generation_output` in routes.py) and the tab's shared
// `batchImages`/`batchVideos`/etc. only ever reflect whichever generation
// currently OWNS the tab's display (see `ownership.ts`) -- never a queued or
// backgrounded generation (e.g. a Video Director shot run) alongside it.
import type { ImageData, VideoData, MeshData } from '$lib/types/tabs';
import type { AudioData } from '$lib/types/audio';
import { leadIndex } from '$lib/generation/leadFile';

export interface GenerationGalleryOutputs {
	images: ImageData[];
	videos: VideoData[];
	audios: AudioData[];
	meshes: MeshData[];
}

/** The tab-display fields a generation's cached outputs resolve to: its
 *  batch arrays verbatim, plus the lead item (the newest derived item when
 *  one exists, else the first) across all four kinds in the persisted
 *  file-index order image/video/audio/mesh (matches `leadFile.ts`). */
export interface LeadOutputPatch {
	batchImages: ImageData[];
	batchVideos: VideoData[];
	batchAudios: AudioData[];
	batchMeshes: MeshData[];
	workbenchIndex: number;
	workbenchTotal: number;
	current_image: string | null;
	current_video: string | null;
	current_audio: AudioData | null;
	current_mesh: string | null;
	file_type: 'image' | 'video' | 'audio' | 'mesh' | null;
}

/** Resolves what a generation's cached outputs should show on the tab --
 *  used both to hydrate a newly-adopted owner with whatever it already
 *  produced while backgrounded (`beginGenerationOwnership`) and to restore a
 *  completing owner's batch arrays from its own cache (`complete.ts`), never
 *  inferred from the tab's prior display state, which may belong to a
 *  different run. `file_type` is `null` (and every batch array empty) when
 *  this generation has produced nothing yet. */
export function leadOutputPatch(outputs: GenerationGalleryOutputs): LeadOutputPatch {
	const { images, videos, audios, meshes } = outputs;
	const all: Array<ImageData | VideoData | AudioData | MeshData> = [...images, ...videos, ...audios, ...meshes];
	const workbenchIndex = leadIndex(all);
	const item = all[workbenchIndex] as { url?: string; originalUrl?: string } | undefined;

	let fileType: LeadOutputPatch['file_type'] = null;
	if (all.length > 0) {
		if (workbenchIndex >= images.length + videos.length + audios.length) fileType = 'mesh';
		else if (workbenchIndex >= images.length + videos.length) fileType = 'audio';
		else if (workbenchIndex >= images.length) fileType = 'video';
		else fileType = 'image';
	}

	return {
		batchImages: images,
		batchVideos: videos,
		batchAudios: audios,
		batchMeshes: meshes,
		workbenchIndex,
		workbenchTotal: all.length,
		current_image: fileType === 'image' ? item?.url || item?.originalUrl || null : null,
		current_video: fileType === 'video' ? item?.url || item?.originalUrl || null : null,
		current_audio: fileType === 'audio' ? (item as AudioData) : null,
		current_mesh: fileType === 'mesh' ? item?.url || item?.originalUrl || null : null,
		file_type: fileType
	};
}

const outputsByGenerationId = new Map<string, GenerationGalleryOutputs>();

function empty(): GenerationGalleryOutputs {
	return { images: [], videos: [], audios: [], meshes: [] };
}

/** Reads a generation's cached outputs without clearing them -- used by
 *  `gallery_update` itself to merge an incremental payload (e.g. one message
 *  carrying only videos) onto what that same generation already produced. */
export function peekGenerationOutputs(generationId: string | undefined): GenerationGalleryOutputs {
	if (!generationId) return empty();
	return outputsByGenerationId.get(generationId) ?? empty();
}

export function setGenerationOutputs(generationId: string | undefined, outputs: GenerationGalleryOutputs): void {
	if (!generationId) return;
	outputsByGenerationId.set(generationId, outputs);
}

/** Reads and forgets a generation's cached outputs -- called once its
 *  terminal event (complete/error/cancelled) has consumed them, so the cache
 *  never outlives the generation it belongs to. */
export function takeGenerationOutputs(generationId: string | undefined): GenerationGalleryOutputs {
	if (!generationId) return empty();
	const outputs = outputsByGenerationId.get(generationId) ?? empty();
	outputsByGenerationId.delete(generationId);
	return outputs;
}
