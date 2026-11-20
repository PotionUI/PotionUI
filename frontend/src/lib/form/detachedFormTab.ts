import { derived, writable } from 'svelte/store';
import { buildSectionStorageKey, foldedForScope } from '$lib/components/form-fields/sectionState';
import type { SectionCollapsedContext } from '$lib/form/sectionCollapsedContext';
import { DEFAULT_PROMPT_PANEL_WIDTH } from '$lib/stores/generationLayout';
import type { GenerationTab } from '$lib/types/tabs';

export interface DetachedFormInput {
	id: string;
	presetId: string;
	mode: string;
	variant?: string | null;
	formData: Record<string, unknown>;
}

export function detachedFormTab(input: DetachedFormInput): GenerationTab {
	return {
		kind: 'generation',
		id: input.id,
		name: input.id,
		selectedPreset: input.presetId,
		selectedMode: input.mode,
		selectedVariant: input.variant ?? null,
		selectedSessionId: null,
		activeGenerationId: null,
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: input.formData,
		variables: {},
		generation: {
			isGenerating: false,
			currentGeneration: null,
			currentProgress: null,
			pipeTimers: {},
			startedAt: null,
			totalTime: null,
			lastDurationMs: null,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			batchMeshes: [],
			artifacts: [],
			workbenchIndex: 0,
			workbenchTotal: 0,
			queue: [],
			submittedPromptTemplate: null
		},
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		leftPanelCollapsed: false,
		workbenchCollapsed: false,
		layoutMode: 'two',
		promptPanelWidth: DEFAULT_PROMPT_PANEL_WIDTH,
		autoTagIds: [],
		autoCollectionIds: [],
		soundOnComplete: false,
		soundOnError: false,
		color: null
	};
}

const remembered = writable<Record<string, boolean>>({});

export function localSectionCollapsed(presetId: string, mode: string): SectionCollapsedContext {
	return {
		folded: derived(remembered, ($remembered) => foldedForScope($remembered, presetId, mode)),
		set(fieldPath: string, collapsed: boolean): void {
			const key = buildSectionStorageKey(presetId, mode, fieldPath);
			if (key) remembered.update((current) => ({ ...current, [key]: collapsed }));
		}
	};
}
