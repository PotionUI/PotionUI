import { logger } from '$lib/utils/logger';
import { richTextToPlainText } from '$lib/utils/richTextUtils';
import type { GenerationRequest, PromptPair, SegmentInput } from '$lib/services/api/index';
import type { Tab, ImageData, VideoData, MeshData } from '$lib/types/tabs';
import type { AudioData } from '$lib/types/audio';
import type { PromptTabData } from '$lib/types/tabs';
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

/** Shuffle a single chip value from its pool of all values. */
export function shuffleChip(chip: any): any {
	if (!chip.shuffle || !chip.allValues || chip.allValues.length <= 1) {
		return chip;
	}
	const available = chip.allValues.filter((v: any) => v.id !== chip.valueId);
	if (available.length === 0) return chip;

	const random = available[Math.floor(Math.random() * available.length)];
	return {
		...chip,
		valueId: random.id,
		label: random.label,
		value: random.value
	};
}

/**
 * Process a list of segments, shuffling any chips that have shuffle mode
 * enabled. Returns `{ segments, changed }`.
 */
function processSegmentsWithShuffle(segments: any[]): {
	segments: any[];
	changed: boolean;
} {
	let changed = false;
	const result = segments.map((segment) => {
		if (!segment.chips || Object.keys(segment.chips).length === 0) return segment;

		const updatedChips: Record<string, any> = {};
		let segmentChanged = false;

		for (const [chipId, chipData] of Object.entries(segment.chips)) {
			const shuffled = shuffleChip(chipData);
			updatedChips[chipId] = shuffled;
			if (shuffled !== chipData) {
				segmentChanged = true;
				changed = true;
			}
		}

		return segmentChanged ? { ...segment, chips: updatedChips } : segment;
	});

	return { segments: result, changed };
}

// ---------------------------------------------------------------------------
// Prompt assembly
// ---------------------------------------------------------------------------

/**
 * Build the `prompts` array that will be sent to the API.
 * Handles both single-prompt and multi-prompt modes, and applies chip shuffling.
 */
export function buildPromptsArray(
	tab: Tab,
	numPrompts: number,
	join: SegmentJoin = 'comma'
): {
	prompts: PromptPair[];
	shuffledPositive: any[];
	shuffledNegative: any[];
	hasShuffled: boolean;
} {
	let hasShuffled = false;

	if (numPrompts > 1 && tab.promptTabs && tab.promptTabs.length > 0) {
		const prompts: PromptPair[] = tab.promptTabs.slice(0, numPrompts).map((promptTab: PromptTabData) => {
			const { segments: posSegs, changed: positiveChanged } = processSegmentsWithShuffle([
				...(promptTab.promptSegments || [])
			]);
			const { segments: negSegs, changed: negativeChanged } = processSegmentsWithShuffle([
				...(promptTab.negativePromptSegments || [])
			]);
			if (positiveChanged || negativeChanged) hasShuffled = true;

			return {
				positive: posSegs.length > 0 ? combineSegmentsToString(posSegs, join) : promptTab.prompt || '',
				negative: negSegs.length > 0 ? combineSegmentsToString(negSegs, join) : promptTab.negativePrompt || ''
			};
		});

		return { prompts, shuffledPositive: [], shuffledNegative: [], hasShuffled };
	}

	// Single-prompt mode
	const { segments: posSegs, changed: posChanged } = processSegmentsWithShuffle([...(tab.promptSegments || [])]);
	const { segments: negSegs, changed: negChanged } = processSegmentsWithShuffle([
		...(tab.negativePromptSegments || [])
	]);
	hasShuffled = posChanged || negChanged;

	const positive = posSegs.length > 0 ? combineSegmentsToString(posSegs, join) : tab.prompt;
	const negative = negSegs.length > 0 ? combineSegmentsToString(negSegs, join) : tab.negativePrompt;

	return {
		prompts: [{ positive: positive.trim(), negative: negative.trim() }],
		shuffledPositive: posSegs,
		shuffledNegative: negSegs,
		hasShuffled
	};
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
 * Shared by every request-assembly site (this file's `startGeneration` and
 * generate/+page.svelte's own, the one actually wired to the Generate button)
 * so the copies can't drift.
 */
export function buildVariablesPayload(tab: Tab, options?: VariablesForSubmitOptions): VariablesPayloadResult {
	const { wireMap, rolls } = buildVariablesForSubmit(tab.variables, options);
	return {
		variables: Object.keys(wireMap).length > 0 ? wireMap : undefined,
		rolls
	};
}
// ---------------------------------------------------------------------------
// Dependencies injected by the page
// ---------------------------------------------------------------------------

export interface GenerationOrchestrationDeps {
	api: {
		startGeneration(req: GenerationRequest): Promise<{
			success: boolean;
			data?: { generation_id: string; status: any };
		}>;
		cancelGeneration(id: string): Promise<unknown>;
		getGenerationStatus(id: string): Promise<{ success: boolean; data?: any }>;
		getGenerationById(id: string, ...args: any[]): Promise<{ success: boolean; data?: any }>;
	};
}

// ---------------------------------------------------------------------------
// startGeneration
// ---------------------------------------------------------------------------

export interface StartGenerationParams {
	tab: Tab;
	activeTabId: string;
	numPrompts: number;
}

export interface StartGenerationResult {
	generationId: string;
	status: any;
	shuffledPositive: any[];
	shuffledNegative: any[];
	hasShuffledChips: boolean;
	/** Fresh choice-variable rolls from this submission — see VariablesPayloadResult.rolls. */
	variableRolls: Record<string, VariableRoll>;
}

/**
 * Assemble form data and call the API to kick off a generation.
 * Throws on validation failure or API error.
 */
export async function startGeneration(
	params: StartGenerationParams,
	deps: GenerationOrchestrationDeps
): Promise<StartGenerationResult> {
	const { tab, numPrompts } = params;

	// Build prompt array (including shuffle)
	const { prompts, shuffledPositive, shuffledNegative, hasShuffled } = buildPromptsArray(tab, numPrompts);

	// Validate
	const hasValidPrompt = prompts.some((p) => p.positive.trim().length > 0);
	if (!tab.selectedPreset || !hasValidPrompt) {
		throw new Error('Missing preset or prompt');
	}

	// No single-in-flight guard: generations are queued server-side, one slot per
	// backend, so any tab may enqueue at any time. See src/core/generation/queue.py.

	const promptState = {
		prompt: tab.prompt,
		negativePrompt: tab.negativePrompt,
		promptSegments: tab.promptSegments,
		negativePromptSegments: tab.negativePromptSegments,
		promptTabs: tab.promptTabs,
		activePromptTab: tab.activePromptTab,
		promptRelay: tab.promptRelay,
		videoDirector: tab.videoDirector,
		musicDirector: tab.musicDirector
	};

	// Variable DEFINITIONS never ride inside the prompt text — there is no
	// `${name=value}` assignment syntax; dynamicprompts binds them out of
	// band via GenerationRequest.variables (src/features/generation/dto.py),
	// consumed by expander.py's _base_context(). Segments only ever contain
	// the USAGE form `${name}`.
	const variablesResult = buildVariablesPayload(tab);

	const request: GenerationRequest = {
		preset_id: tab.selectedPreset,
		prompts,
		mode: tab.selectedMode ?? undefined,
		form_data: tab.formData,
		backend_id: tab.selectedBackendId ?? undefined,
		tag_ids: tab.autoTagIds?.length ? tab.autoTagIds : undefined,
		collection_ids: tab.autoCollectionIds?.length ? tab.autoCollectionIds : undefined,
		prompt_state: promptState,
		segments: buildSegmentsPayload(tab, numPrompts),
		variables: variablesResult.variables,
		source_prompt_id: tab.sourcePromptId ?? undefined
	};

	const response = await deps.api.startGeneration(request);

	if (!response.success || !response.data) {
		throw new Error('API did not return a generation_id');
	}

	return {
		generationId: response.data.generation_id,
		status: response.data.status,
		shuffledPositive,
		shuffledNegative,
		hasShuffledChips: hasShuffled,
		variableRolls: variablesResult.rolls
	};
}

// ---------------------------------------------------------------------------
// cancelGeneration
// ---------------------------------------------------------------------------

export async function cancelGeneration(generationId: string, deps: GenerationOrchestrationDeps): Promise<void> {
	await deps.api.cancelGeneration(generationId);
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
