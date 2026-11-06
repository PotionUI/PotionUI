// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { GenerationState, GenerationTab } from '$lib/types/tabs';

const { default: StudioView } = await import('../../src/routes/generate/components/studio/StudioView.svelte');
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

function tabWith(gen: GenerationState): GenerationTab {
	return { id: 'tab-1', name: 'Potion still', prompt: '', formData: {}, generation: gen } as unknown as GenerationTab;
}

let target: HTMLElement | undefined;
let component: { $destroy: () => void } | undefined;

function mount(gen: GenerationState = generation()): HTMLElement {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: StudioView as never,
		target,
		props: {
			tab: tabWith(gen),
			tabHandlers: {},
			presets: [],
			readiness: null,
			isLoading: false,
			isReloadingPreset: false,
			tabModes: [],
			promptRelayActive: false,
			videoDirectorActive: false,
			videoDirectorCaps: null,
			musicDirectorActive: false,
			musicDirectorCaps: null,
			canBatch: false,
			negativePromptSupported: false,
			negativeInert: false,
			promptlessActive: false,
			dynamicFormRefs: {},
			onFormDataChange: () => {},
			onReload: () => {},
			onMoveToWorkbench: () => {}
		}
	});
	flushSync();
	return target;
}

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	component?.$destroy();
	component = undefined;
	target?.remove();
	target = undefined;
});

describe('studio view', () => {
	it('fills the area the page leaves above the deck and holds the canvas, top bar and dock', () => {
		const root = mount().firstElementChild as HTMLElement;
		expect(root.classList.contains('h-full')).toBe(true);
		expect(root.getAttribute('style') ?? '').not.toContain('--deck-height');
		expect(root.querySelector('.studio-canvas')).not.toBeNull();
		expect(root.querySelector('.studio-topbar')).not.toBeNull();
		expect(root.querySelector('.studio-dock')).not.toBeNull();
	});

	it('leaves run status to the deck: no second progress readout over the canvas while generating', () => {
		const root = mount(
			generation({
				isGenerating: true,
				startedAt: Date.now() - 7000,
				currentGeneration: { id: 'run-1', status: 'running' },
				currentProgress: { step: 'Sampling', progress: 0.4, current_step: 'Sampling' }
			})
		);
		expect(root.querySelector('.studio-canvas')!.textContent).not.toMatch(/Sampling|\d\d:\d\d/);
		expect(root.querySelector('[role="progressbar"]')).toBeNull();
	});

	it('uses no sub-12px text in the top bar, canvas or dock', () => {
		const root = mount();
		const small = [...root.querySelectorAll('*')].filter((el) => el.classList.contains('text-2xs') || el.classList.contains('text-xs'));
		expect(small).toEqual([]);
	});
});

describe('studio view dock height', () => {
	function observeDock() {
		const state = { height: 96, notify: () => {} };
		class FakeResizeObserver {
			constructor(callback: ResizeObserverCallback) {
				state.notify = () => callback([], this as unknown as ResizeObserver);
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		}
		vi.stubGlobal('ResizeObserver', FakeResizeObserver);
		vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
			return this.classList.contains('studio-dock') ? state.height : 0;
		});
		return state;
	}

	it('publishes the prompt row height for the stages that sit above it', () => {
		observeDock();
		const root = mount().firstElementChild as HTMLElement;
		expect(root.style.getPropertyValue('--studio-dock-height')).toBe('96px');
	});

	it('follows the dock when it grows', () => {
		const dock = observeDock();
		const root = mount().firstElementChild as HTMLElement;
		dock.height = 148;
		dock.notify();
		expect(root.style.getPropertyValue('--studio-dock-height')).toBe('148px');
	});
});
