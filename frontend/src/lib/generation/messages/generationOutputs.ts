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

/** Ids `retireGeneration` has permanently closed the door on -- bounded (FIFO
 *  eviction) so a long session retiring many generations can't grow this
 *  forever; the eviction only means a VERY old id could theoretically be
 *  cached again, which is harmless since nothing still references it by
 *  then. */
const RETIRED_GENERATION_ID_LIMIT = 200;
const retiredGenerationIds = new Set<string>();

function rememberRetired(generationId: string): void {
	if (retiredGenerationIds.has(generationId)) return;
	if (retiredGenerationIds.size >= RETIRED_GENERATION_ID_LIMIT) {
		const oldest = retiredGenerationIds.values().next().value;
		if (oldest !== undefined) retiredGenerationIds.delete(oldest);
	}
	retiredGenerationIds.add(generationId);
}

/** True once `retireGeneration` has closed this id out -- guards against a
 *  late/reordered `gallery_update` (or any other producer) recreating a
 *  cache entry for a generation nothing is watching any more. */
export function isGenerationOutputsRetired(generationId: string | undefined): boolean {
	return !!generationId && retiredGenerationIds.has(generationId);
}

export function setGenerationOutputs(generationId: string | undefined, outputs: GenerationGalleryOutputs): void {
	if (!generationId || retiredGenerationIds.has(generationId)) return;
	outputsByGenerationId.set(generationId, outputs);
}

/** The one place a generation's cached outputs are dropped AND its WebSocket
 *  subscription is closed together -- called once nothing consumes that
 *  generation any more: its own terminal event (complete/error/cancelled,
 *  whether or not a tab was found to display it), a tab close that removed
 *  its last owning tab, or reconnect reconciliation resolving it terminal.
 *  Marks the id retired (see `isGenerationOutputsRetired`) so a stray event
 *  arriving afterwards can never recreate its cache entry. Idempotent --
 *  retiring an already-retired id repeats the (harmless) unsubscribe call
 *  but touches nothing else. */
export function retireGeneration(generationId: string | undefined, unsubscribe: (id: string) => void): void {
	if (!generationId) return;
	outputsByGenerationId.delete(generationId);
	rememberRetired(generationId);
	unsubscribe(generationId);
}

/** Test-only: production never needs this (retirement is permanent for a
 *  page's lifetime), but a test suite reusing generation ids across
 *  unrelated cases needs a clean slate. */
export function resetGenerationOutputsRetirementForTests(): void {
	retiredGenerationIds.clear();
}

/** The live page's `(generationId) => ws?.unsubscribe(generationId)`,
 *  registered once (see routes/generate/+page.svelte's `onMount`, right
 *  where its `WebSocketService` is created). `tabsStore.removeTab` needs a
 *  way to unsubscribe an orphaned generation's WebSocket subscription, but --
 *  unlike `dispatchGenerationMessage`'s per-call `DispatchDeps` -- has no
 *  caller-supplied `unsubscribe` to thread through: it is called directly by
 *  more than one component (the tab bar's close button, this page's
 *  close-tab keybinding) that share no per-call context. `null` (the default,
 *  and what a component that tears down without ever mounting the socket
 *  leaves it as) makes `retireOrphanedGenerationIds` a no-op unsubscribe --
 *  the cache is still retired either way. */
let generationUnsubscribeHandler: ((generationId: string) => void) | null = null;

export function setGenerationUnsubscribeHandler(handler: ((generationId: string) => void) | null): void {
	generationUnsubscribeHandler = handler;
}

/** Retires every id in `generationIds` using the registered unsubscribe
 *  handler (a no-op when none is registered) -- the entry point
 *  `tabsStore.removeTab` uses for the ids a closed tab was its last
 *  consumer of. */
export function retireOrphanedGenerationIds(generationIds: Iterable<string>): void {
	const unsubscribe = generationUnsubscribeHandler ?? (() => {});
	for (const generationId of generationIds) retireGeneration(generationId, unsubscribe);
}
