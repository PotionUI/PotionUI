// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { GenerationState, GenerationTab } from '$lib/types/tabs';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		createTag: vi.fn(),
		getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		updateGenerationTags: vi.fn().mockResolvedValue({ success: true })
	}
}));

const { default: StudioDock } = await import('../../src/routes/generate/components/studio/StudioDock.svelte');
const { createClassComponent } = await import('svelte/legacy');

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

function finished(count: number, overrides: Partial<GenerationState> = {}): GenerationState {
	return generation({
		currentGeneration: { id: 'gen-1', status: 'completed' },
		batchImages: Array.from({ length: count }, (_, index) => ({ url: `/media/out-${index}.png` })) as never,
		...overrides
	});
}

function tabWith(gen: GenerationState, formData: Record<string, unknown> = {}): GenerationTab {
	return { id: 'tab-1', name: 'Tab', formData, generation: gen } as unknown as GenerationTab;
}

type Mounted = { target: HTMLElement; set: (props: Record<string, unknown>) => void; destroy: () => void };
let mounted: Mounted | undefined;

const onOpenPrompt = vi.fn();
const onOpenSettings = vi.fn();
const onMoveToWorkbench = vi.fn();

function mount(props: Record<string, unknown> = {}): Mounted {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: StudioDock as never,
		target,
		props: {
			tab: tabWith(generation()),
			promptPreviewText: '',
			promptlessActive: false,
			onOpenPrompt,
			onOpenSettings,
			onMoveToWorkbench,
			...props
		}
	});
	flushSync();
	return {
		target,
		set: (next) => {
			component.$set(next);
			flushSync();
		},
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

const q = <T extends Element = HTMLElement>(selector: string) => mounted!.target.querySelector<T>(selector);
const qa = <T extends Element = HTMLElement>(selector: string) => [...mounted!.target.querySelectorAll<T>(selector)];
const button = (label: string) => q<HTMLButtonElement>(`button[aria-label="${label}"]`);

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('studio dock prompt row', () => {
	it('puts the Settings button right after the prompt pill', () => {
		mounted = mount({ promptPreviewText: 'a glass potion' });
		const row = q('[data-studio-prompt-row]')!;
		const buttons = [...row.querySelectorAll('button')];
		expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['Prompt', 'Settings']);
		expect(buttons[0].textContent!.trim()).toBe('a glass potion');
		expect(buttons[1].textContent!.trim()).toBe('');
		expect(buttons[1].className).toContain('w-11');
	});

	it('shows the placeholder until there is a prompt', () => {
		mounted = mount();
		expect(button('Prompt')!.textContent!.trim()).toBe('Describe what you want…');
		mounted.set({ promptPreviewText: 'moonlit lab' });
		expect(button('Prompt')!.textContent!.trim()).toBe('moonlit lab');
	});

	it('opens the prompt and settings sheets from their buttons', () => {
		mounted = mount();
		button('Prompt')!.click();
		button('Settings')!.click();
		expect(onOpenPrompt).toHaveBeenCalledTimes(1);
		expect(onOpenSettings).toHaveBeenCalledTimes(1);
	});

	it('keeps Settings reachable as a labelled, row-filling button when the preset has no prompt', () => {
		mounted = mount({ promptlessActive: true });
		expect(button('Prompt')).toBeNull();
		const settings = button('Settings')!;
		expect(settings.textContent!.trim()).toBe('Settings');
		expect(settings.className).toContain('flex-1');
	});

	it('lists an attached source image between the prompt and Settings and opens settings from it', () => {
		mounted = mount({ tab: tabWith(generation(), { source: { url: '/media/a.png', name: 'a.png' } }) });
		const labels = qa('[data-studio-prompt-row] button').map((b) => b.getAttribute('aria-label'));
		expect(labels).toEqual(['Prompt', 'Attached: a.png — open settings', 'Settings']);
		button('Attached: a.png — open settings')!.click();
		expect(onOpenSettings).toHaveBeenCalledTimes(1);
	});

	it('draws no run controls: the deck owns Generate, Cancel, X/Y, Continuous and progress', () => {
		mounted = mount({ tab: tabWith(generation({ isGenerating: true, currentProgress: { progress: 0.4 } as never })) });
		expect(q('[role="progressbar"]')).toBeNull();
		expect(q('svg circle')).toBeNull();
		expect(q('[data-compare-toggle]')).toBeNull();
		const names = qa('button').map((b) => b.getAttribute('aria-label') ?? '');
		expect(names.some((name) => /generate|cancel|compare|continuous/i.test(name))).toBe(false);
	});

	it('uses no sub-12px text', () => {
		mounted = mount({ tab: tabWith(finished(2, { workbenchIndex: 1 })) });
		const small = qa('*').filter((el) => el.classList.contains('text-2xs') || el.classList.contains('text-xs'));
		expect(small).toEqual([]);
	});
});

describe('studio dock result row', () => {
	it('is absent at rest and while a run is in flight', () => {
		mounted = mount();
		expect(q('[data-studio-result-row]')).toBeNull();
		mounted.set({ tab: tabWith(finished(2, { isGenerating: true })) });
		expect(q('[data-studio-result-row]')).toBeNull();
	});

	it('is absent when the last run did not complete', () => {
		mounted = mount({ tab: tabWith(finished(2, { currentGeneration: { id: 'gen-1', status: 'error' } as never })) });
		expect(q('[data-studio-result-row]')).toBeNull();
	});

	it('sits above the prompt row with one thumbnail per result, Download and Tags', () => {
		mounted = mount({ tab: tabWith(finished(3, { workbenchIndex: 1 })) });
		const row = q('[data-studio-result-row]')!;
		const promptRow = q('[data-studio-prompt-row]')!;
		expect(row.compareDocumentPosition(promptRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		const thumbs = [...row.querySelectorAll('button[aria-label^="Result "]')];
		expect(thumbs.map((b) => b.getAttribute('aria-label'))).toEqual(['Result 1 of 3', 'Result 2 of 3', 'Result 3 of 3']);
		expect(thumbs.map((b) => b.getAttribute('aria-current'))).toEqual(['false', 'true', 'false']);
		expect(row.querySelector('button[aria-label="Download"]')).not.toBeNull();
	});

	it('selects a result through onMoveToWorkbench with its absolute index', () => {
		mounted = mount({ tab: tabWith(finished(3)) });
		button('Result 3 of 3')!.click();
		expect(onMoveToWorkbench).toHaveBeenCalledTimes(1);
		const event = onMoveToWorkbench.mock.calls[0][0] as CustomEvent<{ item: { url: string }; index: number }>;
		expect(event.detail.index).toBe(2);
		expect(event.detail.item.url).toBe('/media/out-2.png');
	});

	it('hides the attached source chip while a run is in flight', () => {
		mounted = mount({ tab: tabWith(generation({ isGenerating: true }), { source: { url: '/media/a.png' } }) });
		const labels = qa('[data-studio-prompt-row] button').map((b) => b.getAttribute('aria-label'));
		expect(labels).toEqual(['Prompt', 'Settings']);
	});
});
