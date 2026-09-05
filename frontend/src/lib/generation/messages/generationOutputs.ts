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

export interface GenerationGalleryOutputs {
	images: ImageData[];
	videos: VideoData[];
	audios: AudioData[];
	meshes: MeshData[];
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
