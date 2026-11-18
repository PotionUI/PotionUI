import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { menuRow, openEditorMenu } from '../helpers/editorMenu';
import { effectGroups, effectTools } from '$lib/effects/catalog';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { draftFor } from '$lib/tool-tabs/video/videoSteps';
import { writeVideoState, EMPTY_SIGNATURE } from '$lib/tool-tabs/video/videoState';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { lookDraft, toolEffectDraft, type ToolEffectParams } from '$lib/tool-tabs/image/steps';
import { resetMediaToolCatalog } from '$lib/media-tools/catalog';
import type { EffectParams } from '$lib/tool-tabs/image/steps';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';
import { EFFECT_CATALOG } from '../fixtures/effectCatalog';

const { default: EffectCardActions } = await import('$lib/components/tool-tabs/frame/EffectCardActions.svelte');
const { default: OptionsPanel } = await import('$lib/components/tool-tabs/frame/OptionsPanel.svelte');
const { default: VideoEditorTab } = await import('$lib/components/tool-tabs/video/VideoEditorTab.svelte');
const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const tools = effectTools(EFFECT_CATALOG);
const groups = effectGroups(tools);
const GRAIN = { amount: 30, size: 1.5, color: false, seed: 0 };

function text(el: Element | null | undefined) {
	return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('EffectCardActions', () => {
	it('names the effect and says Add or Update', () => {
		const add = mountComponent(EffectCardActions, { name: 'Vignette', onapply: () => undefined, onreset: () => undefined });
		expect(text(add.target.querySelector('[data-options-apply]'))).toBe('Add Vignette ↵');
		expect(text(add.target.querySelector('[data-options-reset]'))).toBe('Reset');
		const update = mountComponent(EffectCardActions, { name: 'Vignette', updating: true, onapply: () => undefined, onreset: () => undefined });
		expect(text(update.target.querySelector('[data-options-apply]'))).toBe('Update Vignette ↵');
	});

	it('disables each button on its own and calls back when enabled', () => {
		const onapply = vi.fn();
		const onreset = vi.fn();
		const view = mountComponent(EffectCardActions, { name: 'Film grain', canApply: false, canReset: false, onapply, onreset });
		const applyButton = view.target.querySelector<HTMLButtonElement>('[data-options-apply]')!;
		const resetButton = view.target.querySelector<HTMLButtonElement>('[data-options-reset]')!;
		expect(applyButton.disabled).toBe(true);
		expect(resetButton.disabled).toBe(true);
		view.rerender({ canApply: true });
		flushSync();
		applyButton.click();
		resetButton.click();
		expect(onapply).toHaveBeenCalledTimes(1);
		expect(onreset).not.toHaveBeenCalled();
	});
});

describe('OptionsPanel footer', () => {
	it('shows Reset and Apply by default and drops them when actions is off', () => {
		for (const phone of [false, true]) {
			const shown = mountComponent(OptionsPanel, { title: 'Crop', icon: 'crop', phone, onapply: () => undefined, onreset: () => undefined });
			expect(shown.target.querySelector('[data-options-apply]')).not.toBeNull();
			expect(shown.target.querySelector('[data-options-reset]')).not.toBeNull();
			const hidden = mountComponent(OptionsPanel, { title: 'Effects', icon: 'editor-effects', phone, actions: false, onapply: () => undefined, onreset: () => undefined });
			expect(hidden.target.querySelector('[data-options-apply]')).toBeNull();
			expect(hidden.target.querySelector('[data-options-reset]')).toBeNull();
		}
	});
});

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function pendingLookState() {
	const stack = apply(beginEdit(createStack(), lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
	const pending = { ...lookDraft({ kind: 'look', filter: 'noir', name: 'Noir', intensity: 40 }), target: null };
	return writeImageState({ ...emptyImageState('effects'), stack: { ...stack, pending } });
}

function renderImageEditor(activeTool: string, state: unknown) {
	const host: ToolTabHost = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state: state as ToolTabHost['state'],
		activeTool,
		run: null,
		hint: null,
		phone: false,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	};
	const view = mountComponent(ImageEditorTab, { host });
	return { view, q: <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel), all: (sel: string) => Array.from(view.target.querySelectorAll<HTMLElement>(sel)) };
}

async function tick() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
	flushSync();
}

async function settle() {
	for (let i = 0; i < 6; i++) await Promise.resolve();
	flushSync();
}

describe('Image editor footer', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('sits in the footer of the options panel for Looks, below its body', () => {
		const { q, all } = renderImageEditor('effects', pendingLookState());
		expect(all('[data-options-apply]')).toHaveLength(1);
		expect(all('[data-options-reset]')).toHaveLength(1);
		expect(q('[data-options-apply]')!.closest('[data-options-footer]')).not.toBeNull();
		expect(q('[data-options-apply]')!.closest('[data-options-panel]')).not.toBeNull();
		expect(q('[data-options-apply]')!.closest('[data-options-body]')).toBeNull();
		expect(text(q('[data-options-apply]'))).toBe('Apply look ↵');
	});

	it('sits in the footer of the options panel for Crop', () => {
		const { q, all } = renderImageEditor('crop', emptyImageState('crop'));
		expect(all('[data-options-apply]')).toHaveLength(1);
		expect(q('[data-options-apply]')!.closest('[data-options-footer]')).not.toBeNull();
		expect(text(q('[data-options-apply]'))).toContain('Apply crop');
	});

	it('has no Apply for Light, and Reset is in the panel footer and the Edit menu', async () => {
		const { q, all } = renderImageEditor('light', emptyImageState('light'));
		expect(all('[data-options-apply]')).toHaveLength(0);
		expect(all('[data-options-footer] [data-options-reset]')).toHaveLength(1);
		await openEditorMenu(q('[data-editor-menubar]')!.parentElement!, 'edit');
		expect(text(menuRow('edit.reset'))).toContain('Reset');
	});

	it('applies the open effect on Enter', async () => {
		const { all } = renderImageEditor('effects', pendingLookState());
		const rows = () => all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);
		expect(rows()).toEqual(['original', 'applied', 'pending']);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
		await settle();
		expect(rows()).toEqual(['original', 'applied', 'current']);
	});
});

function grainParams(amount: number): ToolEffectParams {
	return { kind: 'effect', tool: 'film_grain', name: 'Film grain', values: { ...GRAIN, amount } };
}

function grainState(...amounts: number[]) {
	const stack = amounts.reduce((state, amount) => apply(beginEdit(state, toolEffectDraft(grainParams(amount), EFFECT_CATALOG))), createStack());
	return writeImageState({ ...emptyImageState('effects'), stack });
}

describe('Image effect entries', () => {
	beforeEach(() => {
		resetMediaToolCatalog(EFFECT_CATALOG);
	});

	afterEach(() => {
		resetMediaToolCatalog([]);
	});

	const rows = (view: ReturnType<typeof renderImageEditor>) => view.all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);
	const grainInput = (view: ReturnType<typeof renderImageEditor>) => view.q<HTMLInputElement>('[data-entry-options="film_grain"] input[type="range"], [data-entry-options="film_grain"] input[type="number"]')!;

	it('opens the stored Film grain step in its own panel with its values and no Apply', async () => {
		const view = renderImageEditor('film_grain', grainState(55));
		await tick();
		expect(grainInput(view).value).toBe('55');
		expect(view.q('[data-options-apply]')).toBeNull();
		expect(rows(view)).toEqual(['original', 'current']);
		expect(view.q('[data-tool-rail] button[data-tool="film_grain"] [data-tool-applied]')).not.toBeNull();
	});

	it('edits the latest of two stored copies live, without a new step', async () => {
		const view = renderImageEditor('film_grain', grainState(20, 70));
		await tick();
		expect(grainInput(view).value).toBe('70');
		grainInput(view).value = '80';
		grainInput(view).dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(rows(view)).toEqual(['original', 'applied', 'current']);
	});

	it('moves Film grain in one undo step per drag, however long the drag pauses', async () => {
		vi.useFakeTimers();
		try {
			const view = renderImageEditor('film_grain', grainState(55));
			await tick();
			const drag = async (value: string) => {
				grainInput(view).value = value;
				grainInput(view).dispatchEvent(new Event('input', { bubbles: true }));
				await tick();
			};
			await drag('60');
			await vi.advanceTimersByTimeAsync(1500);
			await drag('65');
			expect(rows(view)).toEqual(['original', 'current']);
			grainInput(view).dispatchEvent(new Event('change', { bubbles: true }));
			await tick();
			view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
			await tick();
			expect(grainInput(view).value).toBe('55');
			expect(rows(view)).toEqual(['original', 'current']);
		} finally {
			vi.useRealTimers();
		}
	});

	it('Reset removes the Film grain step', async () => {
		const view = renderImageEditor('film_grain', grainState(55));
		await tick();
		view.q<HTMLButtonElement>('[data-options-reset]')!.click();
		await tick();
		expect(rows(view)).toEqual(['current']);
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		await tick();
		expect(rows(view)).toEqual(['original', 'current']);
		expect(grainInput(view).value).toBe('55');
	});

	it('another effect from the catalog is its own entry with Add effect and Apply', async () => {
		const view = renderImageEditor('crop', emptyImageState('crop'));
		await tick();
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="retro-tape.scanlines"]')!.click();
		await settle();
		expect(text(view.q('[data-options-apply]'))).toBe('Add effect ↵');
		expect(view.q('[data-entry-options="retro-tape.scanlines"]')).not.toBeNull();
		expect(rows(view)).toEqual(['original', 'pending']);
	});
});

const CLIP: MediaRef = { path: 'uploads/dock.mp4', relative_path: 'uploads/dock.mp4', url: '/uploads/dock.mp4', name: 'dock.mp4', type: 'video', item_id: 'v-1' };
const effectMap = new Map(tools.map((entry) => [entry.id, entry]));

function videoGrainState(...amounts: number[]) {
	const stack = amounts.reduce((state, amount) => apply(beginEdit(state, draftFor('film_grain', { ...GRAIN, amount }, effectMap))), createStack<Record<string, unknown>>());
	return writeVideoState({ stack, tool: 'trim', playhead: 0, loop: true, savedSignature: EMPTY_SIGNATURE, saved: null, renderSignature: null, renderMode: null, renderStartedAt: null } as never);
}

function renderVideoEditor(state: unknown) {
	const host: ToolTabHost = {
		tabId: 'tab-v',
		tool: 'video',
		source: CLIP,
		state: state as ToolTabHost['state'],
		activeTool: 'trim',
		run: null,
		hint: null,
		phone: false,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	};
	const view = mountComponent(VideoEditorTab, { host });
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	const video = q<HTMLVideoElement>('video')!;
	Object.defineProperty(video, 'duration', { value: 8, configurable: true });
	video.dispatchEvent(new Event('loadedmetadata'));
	flushSync();
	return { view, q, all: (sel: string) => Array.from(view.target.querySelectorAll<HTMLElement>(sel)) };
}

describe('Video effect entries', () => {
	beforeEach(() => {
		resetMediaToolCatalog(EFFECT_CATALOG);
	});

	afterEach(() => {
		resetMediaToolCatalog([]);
	});

	const input = (view: ReturnType<typeof renderVideoEditor>) => view.q<HTMLInputElement>('[data-entry-options] input[type="range"], [data-entry-options] input[type="number"]')!;
	const amount = (view: ReturnType<typeof renderVideoEditor>) => input(view).value;
	const rows = (view: ReturnType<typeof renderVideoEditor>) => view.all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);

	async function open(view: ReturnType<typeof renderVideoEditor>, id: string) {
		view.q<HTMLButtonElement>(`[data-tool-rail] [data-tool="${id}"]`)!.click();
		await tick();
	}

	it('shows the latest copy of an effect already in the edits and leaves it applied', async () => {
		const view = renderVideoEditor(videoGrainState(20, 70));
		await open(view, 'film_grain');
		expect(view.q('[data-options-apply]')).toBeNull();
		expect(amount(view)).toBe('70');
		expect(rows(view)).toEqual(['original', 'applied', 'current']);
	});

	it('keeps a changed new effect as a pending step without an Apply button, and applies it on leaving', async () => {
		const view = renderVideoEditor(videoGrainState(20));
		await open(view, 'retro-tape.scanlines');
		expect(view.q('[data-options-apply]')).toBeNull();
		const scanlines = input(view);
		scanlines.value = String(Number(scanlines.value) + 1);
		scanlines.dispatchEvent(new Event('input', { bubbles: true }));
		await tick();
		expect(view.q('[data-options-apply]')).toBeNull();
		expect(rows(view)).toEqual(['original', 'applied', 'pending']);
		await open(view, 'film_grain');
		expect(rows(view)).toEqual(['original', 'applied', 'current']);
		expect(amount(view)).toBe('20');
	});

	it('moves an applied effect live into its one step, one undo per drag', async () => {
		const view = renderVideoEditor(videoGrainState(70));
		await open(view, 'film_grain');
		for (const value of ['75', '80', '85']) {
			input(view).value = value;
			input(view).dispatchEvent(new Event('input', { bubbles: true }));
			await tick();
		}
		expect(rows(view)).toEqual(['original', 'current']);
		expect(view.q('[data-options-apply]')).toBeNull();
		expect(amount(view)).toBe('85');
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
		await tick();
		expect(amount(view)).toBe('70');
		expect(rows(view)).toEqual(['original', 'current']);
	});

	it('Reset puts the defaults back without removing the step', async () => {
		const view = renderVideoEditor(videoGrainState(70));
		await open(view, 'film_grain');
		view.q<HTMLButtonElement>('[data-options-reset]')!.click();
		await tick();
		expect(amount(view)).toBe('30');
		expect(rows(view)).toEqual(['original', 'current']);
	});
});
