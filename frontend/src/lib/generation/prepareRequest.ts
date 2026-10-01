import type { GenerationRequest, PromptPair } from '$lib/services/api';
import type { Segment } from '$lib/types/segments';
import type { Tab } from '$lib/types/tabs';
import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
import type { PredecessorOutputLike } from '$lib/utils/directorContinuation';
import type { SegmentJoin } from '$lib/utils/richSegments';
import type { VariableRoll } from '$lib/utils/variableDefs';
import { assembleDirectorRequest } from '$lib/generation/requestAssembly';
import { buildSegmentsPayload, buildVariablesPayload } from '$lib/utils/generationOrchestrator';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { resolveRequestContext } from '$lib/generation/requestContext';

export type RequestTab = Pick<
	Tab,
	| 'selectedPreset'
	| 'selectedMode'
	| 'selectedVariant'
	| 'formData'
	| 'prompt'
	| 'negativePrompt'
	| 'promptSegments'
	| 'negativePromptSegments'
	| 'promptTabs'
	| 'activePromptTab'
	| 'promptRelay'
	| 'videoDirector'
	| 'musicDirector'
	| 'variables'
	| 'autoTagIds'
	| 'autoCollectionIds'
	| 'sourcePromptId'
	| 'directorRuns'
>;

export interface RandomSources {
	random?: () => number;
	now?: () => number;
}

export interface BuildRequestOptions {
	tabId: string;
	prompts: PromptPair[];
	formData: Record<string, unknown>;
	variables: Record<string, string> | undefined;
	numPrompts: number;
	promptStateVariables: boolean;
}

export function buildGenerationRequest(tab: RequestTab, options: BuildRequestOptions): GenerationRequest {
	return {
		preset_id: tab.selectedPreset!,
		prompts: options.prompts,
		mode: tab.selectedMode ?? undefined,
		form_name: tab.selectedVariant ?? undefined,
		form_data: options.formData,
		tag_ids: tab.autoTagIds?.length ? tab.autoTagIds : undefined,
		collection_ids: tab.autoCollectionIds?.length ? tab.autoCollectionIds : undefined,
		variables: options.variables,
		tab_id: options.tabId,
		source_prompt_id: tab.sourcePromptId ?? undefined,
		prompt_state: {
			prompt: tab.prompt,
			negativePrompt: tab.negativePrompt,
			promptSegments: tab.promptSegments,
			negativePromptSegments: tab.negativePromptSegments,
			...(options.promptStateVariables ? { variables: tab.variables } : {}),
			promptTabs: tab.promptTabs,
			activePromptTab: tab.activePromptTab,
			promptRelay: tab.promptRelay,
			videoDirector: tab.videoDirector
		},
		segments: buildSegmentsPayload(tab as Tab, options.numPrompts)
	};
}

function shuffleChip(chip: any, random: () => number): any {
	if (!chip.shuffle || !chip.allValues || chip.allValues.length <= 1) {
		return chip;
	}
	const availableValues = chip.allValues.filter((v: any) => v.id !== chip.valueId);
	if (availableValues.length === 0) {
		return chip;
	}
	const randomValue = availableValues[Math.floor(random() * availableValues.length)];
	return {
		...chip,
		valueId: randomValue.id,
		label: randomValue.label,
		value: randomValue.value
	};
}

export function shuffleSegmentChips(
	segments: Segment[] | undefined,
	random: () => number = Math.random
): { segments: Segment[]; changed: boolean } {
	const shuffled = [...(segments || [])];
	let changed = false;
	for (let i = 0; i < shuffled.length; i++) {
		const segment = shuffled[i];
		if (segment.chips && Object.keys(segment.chips).length > 0) {
			const updatedChips: Record<string, any> = {};
			let segmentUpdated = false;
			for (const [chipId, chipData] of Object.entries(segment.chips)) {
				const next = shuffleChip(chipData, random);
				updatedChips[chipId] = next;
				if (next !== chipData) {
					segmentUpdated = true;
					changed = true;
				}
			}
			if (segmentUpdated) {
				shuffled[i] = { ...segment, chips: updatedChips };
			}
		}
	}
	return { segments: shuffled, changed };
}

export interface DirectorContext {
	caps: DirectorCapabilities;
	checked: Set<string>;
	predecessorOutputs: Record<string, PredecessorOutputLike> | null;
}

export interface PrepareRequestOptions extends RandomSources {
	tab: RequestTab;
	tabId: string;
	numPrompts: number;
	segmentJoin: SegmentJoin;
	promptRelayActive: boolean;
	promptlessActive: boolean;
	videoDirector: DirectorContext | null;
	musicDirector: { caps: MusicDirectorCapabilities } | null;
}

export type PreparedRequest =
	| {
			ok: true;
			request: GenerationRequest;
			shuffled: { changed: boolean; promptSegments: Segment[]; negativePromptSegments: Segment[] };
			variableRolls: Record<string, VariableRoll>;
			submittedPromptTemplate: PromptPair | null;
			director: { primaryShotIds: string[]; remainingShotIds: string[]; value: VideoDirectorValue | null };
	  }
	| { ok: false; code: 'director' | 'no_preset' | 'no_prompt'; reason: string | null };

export function prepareRequest(options: PrepareRequestOptions): PreparedRequest {
	const { tab } = options;
	const random = options.random ?? Math.random;
	const positive = shuffleSegmentChips(tab.promptSegments, random);
	const negative = shuffleSegmentChips(tab.negativePromptSegments, random);
	const hasShuffledChips = positive.changed || negative.changed;

	let promptsArray: PromptPair[];
	let formDataForRequest: Record<string, unknown> = tab.formData;
	let primaryShotIds: string[] = [];
	let remainingShotIds: string[] = [];
	let directorValue: VideoDirectorValue | null = null;

	if (options.videoDirector) {
		const assembled = assembleDirectorRequest({
			formData: tab.formData,
			videoDirectorActive: true,
			videoDirectorCaps: options.videoDirector.caps,
			videoDirectorValue: tab.videoDirector,
			directorRuns: tab.directorRuns,
			directorChecked: options.videoDirector.checked,
			predecessorOutputs: options.videoDirector.predecessorOutputs,
			musicDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorValue: null
		});
		if (assembled.kind === 'video' && !assembled.ok) {
			return { ok: false, code: 'director', reason: assembled.reason };
		}
		if (assembled.kind !== 'video' || !assembled.ok) {
			return { ok: false, code: 'director', reason: null };
		}
		formDataForRequest = assembled.formData;
		promptsArray = assembled.prompts;
		primaryShotIds = assembled.primaryShotIds;
		remainingShotIds = assembled.remainingShotIds;
		directorValue = assembled.directorValue;
	} else if (options.musicDirector) {
		const assembled = assembleDirectorRequest({
			formData: tab.formData,
			videoDirectorActive: false,
			videoDirectorCaps: null,
			videoDirectorValue: null,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: true,
			musicDirectorCaps: options.musicDirector.caps,
			musicDirectorValue: tab.musicDirector
		});
		if (assembled.kind !== 'music') return { ok: false, code: 'director', reason: null };
		formDataForRequest = assembled.formData;
		promptsArray = assembled.prompts;
	} else if (options.promptRelayActive) {
		const relay = tab.promptRelay;
		const segments = (relay?.timeline?.segments || []).slice().sort((a, b) => a.start - b.start);
		const globalPrompt = (relay?.global_prompt || '').trim();
		const joinedSegments = segments.map((s) => (s.text || '').trim()).filter(Boolean).join(' | ');
		formDataForRequest = {
			...tab.formData,
			global_prompt: globalPrompt,
			timeline: relay?.timeline ?? { duration: 5, fps: 24, segments: [] }
		};
		promptsArray = [{ positive: [globalPrompt, joinedSegments].filter(Boolean).join(' | '), negative: '' }];
	} else if (options.numPrompts > 1 && tab.promptTabs && tab.promptTabs.length > 0) {
		promptsArray = tab.promptTabs.slice(0, options.numPrompts).map((promptTab) => {
			const tabPositive = shuffleSegmentChips(promptTab.promptSegments, random).segments;
			const tabNegative = shuffleSegmentChips(promptTab.negativePromptSegments, random).segments;
			return {
				positive: tabPositive.length > 0 ? resolvePromptSegments(tabPositive, options.segmentJoin) : promptTab.prompt || '',
				negative:
					tabNegative.length > 0 ? resolvePromptSegments(tabNegative, options.segmentJoin) : promptTab.negativePrompt || ''
			};
		});
	} else {
		const mergedPrompt =
			positive.segments.length > 0 ? resolvePromptSegments(positive.segments, options.segmentJoin) : tab.prompt;
		const mergedNegativePrompt =
			negative.segments.length > 0 ? resolvePromptSegments(negative.segments, options.segmentJoin) : tab.negativePrompt;
		promptsArray = [{ positive: mergedPrompt.trim(), negative: mergedNegativePrompt.trim() }];
	}

	const hasValidPrompt = promptsArray.some((p) => p.positive.trim().length > 0);
	if (!tab.selectedPreset) return { ok: false, code: 'no_preset', reason: null };
	if (!hasValidPrompt && !options.promptlessActive) return { ok: false, code: 'no_prompt', reason: null };

	const variablesResult = buildVariablesPayload(
		tab as Tab,
		options.random || options.now ? { random: options.random, now: options.now } : undefined
	);
	const request = buildGenerationRequest(tab, {
		tabId: options.tabId,
		prompts: promptsArray,
		formData: formDataForRequest,
		variables: variablesResult.variables,
		numPrompts: options.numPrompts,
		promptStateVariables: true
	});

	return {
		ok: true,
		request,
		shuffled: { changed: hasShuffledChips, promptSegments: positive.segments, negativePromptSegments: negative.segments },
		variableRolls: variablesResult.rolls,
		submittedPromptTemplate: promptsArray[0] ? { positive: promptsArray[0].positive, negative: promptsArray[0].negative } : null,
		director: { primaryShotIds, remainingShotIds, value: directorValue }
	};
}

export interface SessionRequestInput extends RandomSources {
	presetId: string;
	mode: string;
	session: Record<string, unknown>;
	overrides?: Record<string, unknown>;
	presetVars?: Record<string, any>;
	tabId?: string;
}

export function prepareRequestFromSession(input: SessionRequestInput): PreparedRequest {
	const session = structuredClone(input.session ?? {}) as Record<string, unknown>;
	const overrides = structuredClone(input.overrides ?? {}) as Record<string, unknown>;
	const merged: Record<string, unknown> = { ...session, ...overrides };
	if (overrides.formData && typeof overrides.formData === 'object') {
		merged.formData = { ...((session.formData as Record<string, unknown>) || {}), ...(overrides.formData as Record<string, unknown>) };
	}
	const vars = input.presetVars ?? {};
	const tab = {
		promptSegments: [],
		negativePromptSegments: [],
		...merged,
		selectedPreset: input.presetId,
		selectedMode: input.mode,
		selectedVariant: (merged.selectedVariant as string | null | undefined) ?? null,
		formData: (merged.formData as Record<string, unknown>) ?? {},
		prompt: (merged.prompt as string) ?? '',
		negativePrompt: (merged.negativePrompt as string) ?? ''
	} as RequestTab;
	const context = resolveRequestContext(vars, input.mode);
	return prepareRequest({
		tab,
		tabId: input.tabId ?? '',
		numPrompts: context.numPrompts,
		segmentJoin: context.segmentJoin,
		promptRelayActive: context.promptRelayActive,
		promptlessActive: context.promptlessActive,
		videoDirector:
			context.videoDirectorActive && context.videoDirectorCaps
				? { caps: context.videoDirectorCaps, checked: new Set(), predecessorOutputs: null }
				: null,
		musicDirector:
			context.musicDirectorActive && context.musicDirectorCaps ? { caps: context.musicDirectorCaps } : null,
		random: input.random,
		now: input.now
	});
}
