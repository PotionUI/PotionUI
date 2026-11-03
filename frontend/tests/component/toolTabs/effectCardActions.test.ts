import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
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
const { default: VideoToolOptions } = await import('$lib/components/tool-tabs/video/VideoToolOptions.svelte');
const { default: EffectsOptions } = await import('$lib/components/tool-tabs/image/EffectsOptions.svelte');
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

function videoPanel(props: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent(VideoToolOptions, {
		tool: 'effects',
		op: 'film_grain',
		params: GRAIN,
		effect: 'film_grain',
		effects: groups,
		clip: { width: 1920, height: 1080, duration: 8 },
		playhead: 1,
		outputLength: 8,
		onparams: () => undefined,
		onaspect: () => undefined,
		oneffect: () => undefined,
		onsetin: () => undefined,
		onsetout: () => undefined,
		onpreview: () => undefined,
		oncancelpreview: () => undefined,
		onframe: () => undefined,
		onpickaudio: () => undefined,
		onapplyeffect: () => calls.push('apply'),
		onreseteffect: () => calls.push('reset'),
		effectUpdating: false,
		canApplyEffect: true,
		canResetEffect: true,
		...props
	});
	return { view, calls, q: <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel) };
}

describe('Video effect card actions', () => {
	it('renders the actions inside the open card only, under its params', () => {
		const { view, q } = videoPanel();
		const actions = view.target.querySelectorAll('[data-effect-actions]');
		expect(actions).toHaveLength(1);
		const card = q('[data-effect-card="film_grain"]')!;
		expect(card.contains(actions[0])).toBe(true);
		const form = card.querySelector('[data-effect-description]')!;
		expect(form.compareDocumentPosition(actions[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(q('[data-effect-card="fix_colors"] [data-effect-actions]')).toBeNull();
	});

	it('says Add for an effect that is not in the edits and Update for one that is', () => {
		const { view, q } = videoPanel();
		expect(text(q('[data-options-apply]'))).toBe('Add Film grain ↵');
		view.rerender({ effectUpdating: true });
		flushSync();
		expect(text(q('[data-options-apply]'))).toBe('Update Film grain ↵');
	});

	it('follows the disabled flags and calls back', () => {
		const { view, calls, q } = videoPanel({ canApplyEffect: false, canResetEffect: false });
		expect(q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(true);
		expect(q<HTMLButtonElement>('[data-options-reset]')!.disabled).toBe(true);
		view.rerender({ canApplyEffect: true, canResetEffect: true });
		flushSync();
		q<HTMLButtonElement>('[data-options-apply]')!.click();
		q<HTMLButtonElement>('[data-options-reset]')!.click();
		expect(calls).toEqual(['apply', 'reset']);
	});

	it('puts the actions under the form, below the strip, on a phone', () => {
		const { view, q } = videoPanel({ phone: true });
		const strip = q('[data-effect-strip]')!;
		const actions = q('[data-effect-actions]')!;
		expect(view.target.querySelectorAll('[data-effect-actions]')).toHaveLength(1);
		expect(strip.contains(actions)).toBe(false);
		expect(strip.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(text(actions)).toContain('Add Film grain');
	});

	it('has no effect actions on other tools', () => {
		const { view } = videoPanel({ tool: 'speed', op: 'speed', params: { speed: 1 } });
		expect(view.target.querySelector('[data-effect-actions]')).toBeNull();
	});
});

function imageOptions(params: EffectParams | null, props: Record<string, unknown> = {}) {
	const changes: EffectParams[] = [];
	const view = mountComponent(EffectsOptions, {
		params,
		effects: tools,
		onapply: () => changes.push({ kind: 'look', filter: 'applied', name: '', intensity: 0 }),
		onchange: (next: EffectParams) => changes.push(next),
		...props
	});
	return { view, changes, q: <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel) };
}

const GRAIN_PARAMS: EffectParams = { kind: 'effect', tool: 'film_grain', name: 'Film grain', values: { ...GRAIN } };

describe('Image effect card actions', () => {
	it('renders the actions inside the open card, under its params', () => {
		const { view, q } = imageOptions(GRAIN_PARAMS);
		const actions = view.target.querySelectorAll('[data-effect-actions]');
		expect(actions).toHaveLength(1);
		const card = q('[data-effect-card="film_grain"]')!;
		expect(card.dataset.open).toBe('true');
		expect(card.contains(actions[0])).toBe(true);
		expect(q('[data-effect-card="fix_colors"] [data-effect-actions]')).toBeNull();
	});

	it('says Add for a new effect and Update for one reopened from the edits', () => {
		const { q } = imageOptions(GRAIN_PARAMS);
		expect(text(q('[data-options-apply]'))).toBe('Add Film grain ↵');
		document.body.innerHTML = '';
		const reopened = imageOptions(GRAIN_PARAMS, { updating: true, original: { ...GRAIN_PARAMS, values: { ...GRAIN, amount: 50 } } });
		expect(text(reopened.q('[data-options-apply]'))).toBe('Update Film grain ↵');
	});

	it('disables Reset at the defaults and Reset puts the defaults back', () => {
		const atDefaults = imageOptions(GRAIN_PARAMS);
		expect(atDefaults.q<HTMLButtonElement>('[data-options-reset]')!.disabled).toBe(true);
		document.body.innerHTML = '';
		const changed = imageOptions({ ...GRAIN_PARAMS, values: { ...GRAIN, amount: 80 } } as EffectParams);
		const reset = changed.q<HTMLButtonElement>('[data-options-reset]')!;
		expect(reset.disabled).toBe(false);
		reset.click();
		expect(changed.changes).toEqual([{ ...GRAIN_PARAMS, values: GRAIN }]);
	});

	it('disables Update until the values differ from the saved edit', () => {
		const same = imageOptions(GRAIN_PARAMS, { updating: true, original: GRAIN_PARAMS });
		expect(same.q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(true);
		document.body.innerHTML = '';
		const edited = imageOptions({ ...GRAIN_PARAMS, values: { ...GRAIN, amount: 80 } } as EffectParams, { updating: true, original: GRAIN_PARAMS });
		expect(edited.q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(false);
	});

	it('keeps Add enabled for a new effect and calls onapply', () => {
		const { q, changes } = imageOptions(GRAIN_PARAMS);
		const button = q<HTMLButtonElement>('[data-options-apply]')!;
		expect(button.disabled).toBe(false);
		button.click();
		expect(changes).toHaveLength(1);
	});

	it('puts the actions under the form on a phone', () => {
		const { view, q } = imageOptions(GRAIN_PARAMS, { phone: true });
		const strip = q('[data-effect-strip]')!;
		const actions = q('[data-effect-actions]')!;
		expect(view.target.querySelectorAll('[data-effect-actions]')).toHaveLength(1);
		expect(strip.contains(actions)).toBe(false);
		expect(strip.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('shows no actions until something is picked', () => {
		const { view } = imageOptions(null);
		expect(view.target.querySelector('[data-effect-actions]')).toBeNull();
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

	it('is absent for Effects, where the actions sit in the open edit', () => {
		const { q, all } = renderImageEditor('effects', pendingLookState());
		expect(all('[data-options-apply]')).toHaveLength(1);
		expect(all('[data-options-reset]')).toHaveLength(1);
		expect(q('[data-options-apply]')!.closest('[data-effect-actions]')).not.toBeNull();
		expect(text(q('[data-options-apply]'))).toBe('Add Noir ↵');
	});

	it('is present for Crop', () => {
		const { q, all } = renderImageEditor('crop', emptyImageState('crop'));
		expect(all('[data-options-apply]')).toHaveLength(1);
		expect(q('[data-options-apply]')!.closest('[data-effect-actions]')).toBeNull();
		expect(text(q('[data-options-apply]'))).toContain('Apply crop');
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

describe('Image effect opened from the list', () => {
	beforeEach(() => {
		resetMediaToolCatalog(EFFECT_CATALOG);
	});

	afterEach(() => {
		resetMediaToolCatalog([]);
	});

	function open(view: ReturnType<typeof renderImageEditor>, id: string) {
		view.q<HTMLButtonElement>('[data-effects-tab="effects"]')!.click();
		flushSync();
		view.q<HTMLButtonElement>(`[data-effect-card="${id}"] button`)!.click();
		flushSync();
	}

	const rows = (view: ReturnType<typeof renderImageEditor>) => view.all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);

	it('edits the existing step: its values, Update, disabled until a value differs', () => {
		const view = renderImageEditor('effects', grainState(55));
		open(view, 'film_grain');
		const button = view.q<HTMLButtonElement>('[data-options-apply]')!;
		expect(text(button)).toBe('Update Film grain ↵');
		expect(button.disabled).toBe(true);
		expect(view.q<HTMLInputElement>('[data-effect-card="film_grain"] input[type="range"], [data-effect-card="film_grain"] input[type="number"]')!.value).toBe('55');
		expect(rows(view)).toEqual(['original', 'pending']);
	});

	it('shows the defaults and Add for an effect that is not in the edits', () => {
		const view = renderImageEditor('effects', grainState(55));
		open(view, 'retro-tape.scanlines');
		expect(text(view.q('[data-options-apply]'))).toBe('Add Scanlines ↵');
		expect(view.q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(false);
	});

	it('Update replaces the step so the stack keeps its length, and undo restores the old values', async () => {
		const view = renderImageEditor('effects', grainState(55));
		open(view, 'film_grain');
		view.q<HTMLButtonElement>('[data-effect-card="film_grain"] [data-options-reset]')!.click();
		flushSync();
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		view.q<HTMLButtonElement>('[data-options-apply]')!.click();
		await settle();
		expect(rows(view)).toEqual(['original', 'current']);
		expect(view.all('[data-edit-stack] [data-stack-row]')).toHaveLength(2);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
		await settle();
		expect(rows(view)).toEqual(['original', 'current']);
		open(view, 'film_grain');
		expect(view.q<HTMLInputElement>('[data-effect-card="film_grain"] input[type="range"], [data-effect-card="film_grain"] input[type="number"]')!.value).toBe('55');
	});

	it('edits the latest of two copies', () => {
		const view = renderImageEditor('effects', grainState(20, 70));
		open(view, 'film_grain');
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		expect(view.q<HTMLInputElement>('[data-effect-card="film_grain"] input[type="range"], [data-effect-card="film_grain"] input[type="number"]')!.value).toBe('70');
		expect(rows(view)).toEqual(['original', 'applied', 'pending']);
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

describe('Video effect opened from the list', () => {
	beforeEach(() => {
		resetMediaToolCatalog(EFFECT_CATALOG);
	});

	afterEach(() => {
		resetMediaToolCatalog([]);
	});

	const amount = (view: ReturnType<typeof renderVideoEditor>) => view.q<HTMLInputElement>('[data-effect-card="film_grain"] input[type="range"], [data-effect-card="film_grain"] input[type="number"]')!.value;
	const rows = (view: ReturnType<typeof renderVideoEditor>) => view.all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);

	function openEffects(view: ReturnType<typeof renderVideoEditor>) {
		view.q<HTMLButtonElement>('[data-tool-rail] [data-tool="effects"]')!.click();
		flushSync();
	}

	function openCard(view: ReturnType<typeof renderVideoEditor>, id: string) {
		view.q<HTMLButtonElement>(`[data-effect-card="${id}"] button`)!.click();
		flushSync();
	}

	it('edits the latest copy of an effect already in the edits', () => {
		const view = renderVideoEditor(videoGrainState(20, 70));
		openEffects(view);
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		expect(view.q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(true);
		expect(amount(view)).toBe('70');
		expect(rows(view)).toEqual(['original', 'applied', 'pending']);
	});

	it('shows the defaults and Add for an effect that is not in the edits', () => {
		const view = renderVideoEditor(videoGrainState(20));
		openEffects(view);
		openCard(view, 'retro-tape.scanlines');
		expect(text(view.q('[data-options-apply]'))).toBe('Add Scanlines ↵');
		openCard(view, 'film_grain');
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		expect(amount(view)).toBe('20');
	});

	it('Reset puts the defaults back without removing the step', () => {
		const view = renderVideoEditor(videoGrainState(70));
		openEffects(view);
		view.q<HTMLButtonElement>('[data-options-reset]')!.click();
		flushSync();
		expect(amount(view)).toBe('30');
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		expect(rows(view)).toEqual(['original', 'pending']);
	});

	it('Update replaces the step so the stack keeps its length, and undo restores the old values', () => {
		const view = renderVideoEditor(videoGrainState(70));
		openEffects(view);
		view.q<HTMLButtonElement>('[data-options-reset]')!.click();
		flushSync();
		view.q<HTMLButtonElement>('[data-options-apply]')!.click();
		flushSync();
		expect(rows(view)).toEqual(['original', 'pending']);
		expect(amount(view)).toBe('30');
		expect(text(view.q('[data-options-apply]'))).toBe('Update Film grain ↵');
		expect(view.q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(true);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
		flushSync();
		expect(amount(view)).toBe('70');
		expect(rows(view)).toEqual(['original', 'pending']);
	});
});
