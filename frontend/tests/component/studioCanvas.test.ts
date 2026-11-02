// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { GenerationState, GenerationTab } from '$lib/types/tabs';

vi.mock('$lib/generation/compare/compareApi', () => ({
	postGrid: vi.fn(),
	fetchGrid: vi.fn(),
	postRetryFailed: vi.fn(),
	fetchGridSettings: vi.fn(async () => ({ confirm_above: 24, hard_cap: 100 }))
}));

const { default: StudioCanvas } = await import('../../src/routes/generate/components/studio/StudioCanvas.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { armCompare, setAxis } = await import('$lib/generation/compare/compareStore.svelte');

function generation(overrides: Partial<GenerationState> = {}): GenerationState {
	return {
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
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null,
		...overrides
	};
}

const modelFile = {
	artifact_type: 'model_file',
	artifact_data: { index: 0, kind: 'model', model_type: 'diffusion_model', file_name: 'flux-fp8.safetensors', size_bytes: 1, source_size_bytes: 2, format: 'fp8_scaled' }
};

let target: HTMLElement | undefined;
let component: { $destroy: () => void } | undefined;

function mount(tabId: string, gen: GenerationState): HTMLElement {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: StudioCanvas as never,
		target,
		props: { tab: { id: tabId, name: 'Tab', generation: gen } as unknown as GenerationTab }
	});
	flushSync();
	return target;
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	target = undefined;
});

describe('studio canvas stages', () => {
	it('keeps the model file stage clear of the prompt row, and only of the prompt row', () => {
		const root = mount('canvas-model', generation({ artifacts: [modelFile] as never }));
		const stage = root.querySelector<HTMLElement>('[data-testid="studio-model-file-stage"]')!;
		expect(stage.getAttribute('style')).toContain('padding-bottom: calc(var(--studio-dock-height, 14rem) + 0.75rem)');
		expect(stage.getAttribute('style')).not.toContain('--dock-height');
	});

	it('keeps the compare stage clear of the prompt row, and only of the prompt row', () => {
		armCompare('canvas-compare');
		setAxis('canvas-compare', 'x', {
			field: 'sampler',
			type: 'sampler',
			label: 'sampler',
			values: ['euler', 'heun'].map((value) => ({ value, label: value }))
		} as never);
		const root = mount('canvas-compare', generation());
		const stage = root.querySelector<HTMLElement>('[data-testid="studio-compare-stage"]')!;
		expect(stage).not.toBeNull();
		expect(stage.getAttribute('style')).toContain('padding-bottom: calc(var(--studio-dock-height, 7rem) + 0.75rem)');
		expect(stage.getAttribute('style')).not.toMatch(/(^|[^-])--dock-height/);
	});
});
