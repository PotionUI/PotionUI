import { richTextToPlainText } from '$lib/utils/richTextUtils';
import type { SegmentInput } from '$lib/services/api/index';
import type { Tab, ImageData, VideoData, MeshData } from '$lib/types/tabs';
import type { AudioData } from '$lib/types/audio';
import type { Segment } from '$lib/types/segments';
import { flattenRichSegments, type SegmentJoin } from '$lib/utils/richSegments';
import { buildVariablesForSubmit, type VariableRoll, type VariablesForSubmitOptions } from '$lib/utils/variableDefs';

// ---------------------------------------------------------------------------
// Segment/prompt utilities
// ---------------------------------------------------------------------------

/** Combine enabled prompt segments into one plain-text string. */
export function combineSegmentsToString(segments: Segment[], join: SegmentJoin = 'comma'): string {
	return flattenRichSegments(segments, join);
}

// ---------------------------------------------------------------------------
// Structured segment payload
// ---------------------------------------------------------------------------

/** Resolve a segment's chip placeholders to the plain text that is actually sent. */
export function resolveSegmentText(segment: Segment): string {
	if (segment.chips && Object.keys(segment.chips).length > 0) {
		return richTextToPlainText(segment.content, segment.chips);
	}
	return segment.content;
}

/** Map one editor Segment to the backend `SegmentInput` contract. */
export function buildSegmentInput(
	segment: Segment,
	channel: 'positive' | 'negative',
	promptIndex: number,
	segmentIndex: number
): SegmentInput {
	const text = resolveSegmentText(segment);

	const phrasebooks = Object.values(segment.chips || {}).map((c) => ({
		phrasebook_value_id: c.valueId,
		category_path: c.categoryPath,
		value: c.value
	}));

	return {
		channel,
		prompt_index: promptIndex,
		segment_index: segmentIndex,
		segment_type: segment.type === 'break' ? 'break' : 'content',
		text,
		is_disabled: segment.enabled === false || !!segment.isDisabled,
		name: segment.name ?? null,
		color: segment.color ?? null,
		description: segment.description ?? null,
		phrasebooks
	};
}

/**
 * Flatten a tab's positive + negative segments (and, in multi-prompt mode, every
 * prompt tab's segments with its `prompt_index`) into the `SegmentInput[]` that is
 * attached to the generation request. Pure and unit-testable.
 */
export function buildSegmentsPayload(tab: Tab, numPrompts: number): SegmentInput[] {
	const out: SegmentInput[] = [];

	const pushChannel = (segments: Segment[] | undefined, channel: 'positive' | 'negative', promptIndex: number) => {
		(segments || []).forEach((seg, i) => {
			out.push(buildSegmentInput(seg, channel, promptIndex, i));
		});
	};

	if (numPrompts > 1 && tab.promptTabs && tab.promptTabs.length > 0) {
		tab.promptTabs.slice(0, numPrompts).forEach((promptTab, promptIndex) => {
			pushChannel(promptTab.promptSegments, 'positive', promptIndex);
			pushChannel(promptTab.negativePromptSegments, 'negative', promptIndex);
		});
	} else {
		pushChannel(tab.promptSegments, 'positive', 0);
		pushChannel(tab.negativePromptSegments, 'negative', 0);
	}

	return out;
}

export interface VariablesPayloadResult {
	/** The `variables` field of a GenerationRequest, or `undefined` when empty. */
	variables: Record<string, string> | undefined;
	/** Fresh rolls for every `shuffle`-mode choice variable, resolved by THIS
	 *  call. RUN state, not definition state — the caller persists it separately
	 *  (e.g. `Tab.variableRolls`) so usage chips re-render showing the pick.
	 *  Not applied here; this function assembles, the caller decides how to persist. */
	rolls: Record<string, VariableRoll>;
}

/**
 * The `variables` field of a GenerationRequest, mode-aware: `shuffle`-mode
 * choice variables are rolled ONCE right here (every call is a Generate click).
 * Called from generate/+page.svelte, the one place actually wired to the
 * Generate button, so there's a single source of truth for the wire shape.
 */
export function buildVariablesPayload(tab: Tab, options?: VariablesForSubmitOptions): VariablesPayloadResult {
	const { wireMap, rolls } = buildVariablesForSubmit(tab.variables, options);
	return {
		variables: Object.keys(wireMap).length > 0 ? wireMap : undefined,
		rolls
	};
}

// ---------------------------------------------------------------------------
// restoreCompletedGeneration — used by restoreActiveGenerations
// ---------------------------------------------------------------------------

export interface RestoredGenerationData {
	images: ImageData[];
	videos: VideoData[];
	audios: AudioData[];
	meshes: MeshData[];
	totalItems: number;
}

// The history API serializes `files` rows verbatim: `file_type` is UPPERCASE
// ('IMAGE'/'VIDEO'/'MESH', matching the DB) and there is no `url` field - the
// servable URL must be built from the row's `file_path` basename. The
// WebSocket path (galleryUpdate.ts) delivers lowercase types and ready-made
// paths, so anything comparing the two shapes must normalize, not assume.
function fileTypeOf(f: any): string {
	return typeof f?.file_type === 'string' ? f.file_type.toLowerCase() : '';
}

function fileUrlOf(f: any, generationId: string, fallbackName: string): string {
	if (typeof f?.url === 'string' && f.url) return f.url;
	const basename =
		typeof f?.file_path === 'string' && f.file_path ? f.file_path.split('/').pop() : null;
	return `/api/media/generations/${generationId}/${basename || fallbackName}`;
}

export function mapGenerationFiles(files: any[], generationId: string): RestoredGenerationData {
	const images: ImageData[] = (files || [])
		.filter((f: any) => fileTypeOf(f) === 'image')
		.map((f: any, index: number) => {
			const url = fileUrlOf(f, generationId, `${index}.png`);
			return {
				url,
				originalUrl: url,
				derived: f.is_derived === true,
				seed: f.seed,
				resolution: f.resolution,
				sampler: f.sampler,
				cfg: f.cfg,
				step: f.step
			};
		});

	const videos: VideoData[] = (files || [])
		.filter((f: any) => fileTypeOf(f) === 'video')
		.map((f: any, index: number) => {
			const url = fileUrlOf(f, generationId, `${index}.mp4`);
			return {
				url,
				originalUrl: url,
				derived: f.is_derived === true,
				seed: f.seed,
				resolution: f.resolution
			};
		});

	const audios: AudioData[] = (files || [])
		.filter((f: any) => fileTypeOf(f) === 'audio')
		.map((f: any, index: number) => {
			const url = fileUrlOf(f, generationId, `${index}.wav`);
			return {
				url,
				originalUrl: url,
				file_type: 'audio' as const,
				track_type: f.track_type,
				duration: f.duration_seconds,
				sample_rate: f.sample_rate,
				channels: f.channels,
				file_size: f.file_size,
				derived: f.is_derived === true,
				seed: f.seed
			};
		});

	const meshes: MeshData[] = (files || [])
		.filter((f: any) => fileTypeOf(f) === 'mesh')
		.map((f: any, index: number) => {
			const url = fileUrlOf(f, generationId, `${index}.glb`);
			const basename = typeof f.file_path === 'string' ? f.file_path.split('/').pop() : null;
			return {
				url,
				originalUrl: url,
				file_type: 'mesh' as const,
				mesh_name: basename ?? undefined,
				mesh_format: f.mesh_format || (basename?.includes('.') ? basename.split('.').pop() : 'glb'),
				derived: f.is_derived === true,
				seed: f.seed
			};
		});

	return {
		images,
		videos,
		audios,
		meshes,
		totalItems: images.length + videos.length + audios.length + meshes.length
	};
}
