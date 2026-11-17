import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get, writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { appliedSteps, apply, beginEdit, createStack, reopen, updatePending, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
import { basisFor, generateDraft, leftOutCount, leftOutNotice, readGenerateParams, type GenerateParams } from '$lib/tool-tabs/image/generate/generateStep';
import { createRunEngine, runViews } from '$lib/tool-tabs/image/generate/runEngine';
import { outputSize } from '$lib/tool-tabs/image/payload';
import { imageRowInfo, outOfDateCount, outOfDateNotice, stepsBeneath } from '$lib/tool-tabs/image/stepKinds';
import { cropDraft, resizeDraft } from '$lib/tool-tabs/image/steps';
import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
import GenerateFixture from './GenerateFixture.svelte';

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');
const { default: SaveMenuRows } = await import('$lib/components/tool-tabs/frame/SaveMenuRows.svelte');
const { default: DockStaleNotice } = await import('$lib/components/tool-tabs/generate/DockStaleNotice.svelte');
const { default: StaleOutline } = await import('$lib/components/tool-tabs/generate/StaleOutline.svelte');

const SOURCE = { width: 1000, height: 800 };
const offer = (): TaskOffer => ({ preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null });
const edit = (): EditorTaskInfo => ({ id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer()], default: null });
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_2', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's2', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });

const crop = (x: number) => cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x, y: 0, width: 0.5, height: 1 } }, aspect: 'free' }, 'Free', { width: 500, height: 800 });
const resize = (width: number) => resizeDraft({ width, height: Math.round(width * 0.8), lock: true });

function params(beneath: Parameters<typeof basisFor>[0], over: Partial<GenerateParams> = {}): GenerateParams {
	return {
		v: 2,
		task: 'edit_region',
		prompt: 'red sailboat',
		candidates: 3,
		results: [1, 2, 3].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: n })),
		chosen: 1,
		seed: 2,
		rect: { x: 0.2, y: 0.2, width: 0.3, height: 0.3 },
		soft: false,
		setup: { preset_id: 'qwen', mode: 'editor:edit', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: { item_id: 'mask1' }, size: { width: 400, height: 300 } },
		run: { id: 'er_1', status: 'done', error: null },
		basis: basisFor(beneath, outputSize(beneath, SOURCE)),
		...over
	};
}

function stackWith(first: Parameters<typeof beginEdit>[1], second: Parameters<typeof beginEdit>[1]): EditStackState<unknown> {
	let stack = apply(beginEdit(createStack(), first));
	stack = apply(beginEdit(stack, generateDraft(params(appliedSteps(stack)), 'Edit a region', 'sparkles') as never));
	return apply(updatePending(reopen(stack, 's1'), second as never));
}

async function settle() {
	for (let i = 0; i < 60; i++) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
	maskArea.set(null);
});

afterEach(() => {
	maskArea.set(null);
	runViews.set({});
});

describe('an out of date row', () => {
	function mountStack(stack: EditStackState<unknown>) {
		ensureCoreImageEditor();
		const calls: string[] = [];
		const view = mountComponent(EditStack, {
			stack,
			original: { summary: '1000×800 · PNG' },
			rowInfo: (step: never) => imageRowInfo(step, stepsBeneath(stack.steps, stack.steps.findIndex((entry) => entry.id === (step as { id: string }).id))),
			onrowaction: (id: string, action: string) => calls.push(`${id}:${action}`),
			onreopen: () => undefined,
			onseek: () => undefined,
			ontogglehidden: (id: string) => calls.push(`hide:${id}`),
			onremove: (id: string) => calls.push(`remove:${id}`),
			onmove: () => undefined
		});
		const row = () => view.target.querySelector<HTMLElement>('[data-stack-row="s2"]')!;
		return { calls, row, view };
	}
	const clean = (el: Element) => (el.textContent ?? '').replace(/\s+/g, ' ').trim();
	const actions = (row: HTMLElement) => Array.from(row.querySelectorAll('[data-stack-actions] button')).map((b) => b.getAttribute('aria-label'));

	it('says the picture changed in the warning tone, with Redo and Remove and no Hide', () => {
		const { row, calls } = mountStack(stackWith(crop(0), crop(0.25)));
		expect(clean(row())).toContain('The picture changed');
		expect(row().querySelector('[data-stack-state="skipped"] .text-warning')).not.toBeNull();
		expect(row().querySelector('[data-stack-thumb]')).toBeNull();
		expect(actions(row())).toEqual(['Redo on the new picture Edit a region', 'Remove Edit a region']);
		row().querySelector<HTMLButtonElement>('[data-stack-action="redo"]')!.click();
		row().querySelector<HTMLButtonElement>('[data-stack-remove]')!.click();
		expect(calls).toEqual(['s2:redo', 'remove:s2']);
	});

	it('keeps Redo on the row of the stale step while it is the open one', () => {
		const { row, calls } = mountStack(reopen(stackWith(crop(0), crop(0.25)), 's2'));
		expect(row().getAttribute('data-status')).toBe('pending');
		expect(clean(row())).toContain('The picture changed');
		const redo = row().querySelector<HTMLButtonElement>('[data-stack-action="redo"]');
		expect(redo).not.toBeNull();
		expect(redo!.closest('[data-stack-actions]')!.className).not.toContain('opacity-0');
		redo!.click();
		expect(calls).toEqual(['s2:redo']);
	});

	it('offers no row actions on an open step that is up to date', () => {
		const { row } = mountStack(reopen(stackWith(crop(0), crop(0)), 's2'));
		expect(row().getAttribute('data-status')).toBe('pending');
		expect(row().querySelector('[data-stack-action]')).toBeNull();
	});

	it('says it in the dock notice row with a Redo button', () => {
		const calls: string[] = [];
		const view = mountComponent(DockStaleNotice, { onredo: () => calls.push('redo') });
		const notice = view.target.querySelector<HTMLElement>('[data-dock-stale]')!;
		expect(notice.getAttribute('role')).toBe('alert');
		expect(clean(notice)).toContain('The picture changed. Redo on the new picture');
		view.target.querySelector<HTMLButtonElement>('[data-dock-redo]')!.click();
		expect(calls).toEqual(['redo']);
		view.destroy();
	});

	it('is an ordinary done row while the picture beneath has the shape it was made on', () => {
		const { row } = mountStack(stackWith(crop(0), crop(0)));
		expect(clean(row())).toContain('2 of 3 · red sailboat');
		expect(clean(row())).not.toContain('The picture changed');
		expect(row().querySelector('[data-stack-thumb]')).not.toBeNull();
		expect(actions(row())).toContain('Try again Edit a region');
	});

	it('leaves the rows of other steps as they were', () => {
		const { view } = mountStack(stackWith(crop(0), crop(0.25)));
		const first = view.target.querySelector<HTMLElement>('[data-stack-row="s1"]')!;
		expect(actions(first)).toEqual(['Hide Crop', 'Remove Crop']);
		expect(first.querySelector('[data-stack-state]')).toBeNull();
	});
});

describe('the Save menu notice', () => {
	it('lists an out of date step as left out beside one that is still generating', () => {
		const stack = stackWith(crop(0), crop(0.25));
		const flying = apply(beginEdit(stack, generateDraft(params([], { run: { id: 'er_9', status: 'running', error: null }, results: [], basis: undefined }), 'Edit a region', 'sparkles') as never));
		const steps = appliedSteps(flying);
		const notices = [leftOutNotice(leftOutCount(steps)), outOfDateNotice(outOfDateCount(steps))].filter((line): line is string => !!line);
		const view = mountComponent(SaveMenuRows, { replace: { allowed: true }, notices, onchoose: () => undefined });
		const lines = Array.from(view.target.querySelectorAll('[data-save-notices] span')).map((span) => span.textContent);
		expect(lines).toEqual(['1 edit is not included: it is still generating.', '1 edit is not included: the picture beneath it changed.']);
		view.destroy();
	});
});

describe('the outline of the old area', () => {
	it('is drawn where the area was, in the warning colour, and not at all without one', () => {
		const view = mountComponent(StaleOutline, { rect: { x: 0.2, y: 0.25, width: 0.3, height: 0.4 } });
		const outline = view.target.querySelector<HTMLElement>('[data-stale-outline]')!;
		expect(outline.getAttribute('style')).toContain('left: 20%');
		expect(outline.getAttribute('style')).toContain('height: 40%');
		expect(outline.className).toContain('border-warning');
		view.destroy();
		const none = mountComponent(StaleOutline, { rect: null });
		expect(none.target.querySelector('[data-stale-outline]')).toBeNull();
		none.destroy();
	});
});

describe('Redo on the new picture', () => {
	function build(initial: EditStackState<unknown>) {
		let stack = initial;
		const start = vi.fn(async () => run());
		const engine = createRunEngine(
			{ start, get: async () => run(), cancel: async () => run({ status: 'cancelled' }), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
			{ views: writable({}) }
		);
		const deps: ControllerDeps = {
			getStack: () => stack,
			setStack: (next) => (stack = next),
			source: () => ({ ref: { item_id: 'pic' } }),
			editLink: async () => ({ id: 'ed', revision: 1 }),
			landRegion: async () => ({ item_id: 'mask2' }),
			areaSize: () => ({ width: 200, height: 150 }),
			pictureSize: (id) => {
				const list = appliedSteps(stack);
				const at = id ? list.findIndex((step) => step.id === id) : -1;
				return outputSize(at >= 0 ? list.slice(0, at) : list, SOURCE);
			},
			stepsBeneath: async () => [],
			engine,
			ports: {
				tasks: async () => [edit()],
				sessions: async () => [],
				formulas: async () => [],
				schema: async () => ({ properties: {} }),
				setDefault: async () => undefined,
				clearDefault: async () => undefined,
				saveSession: async () => ({ id: 'new', name: 'New', version: 1, updated_at: null }),
				updateSession: async () => ({ id: 's1', name: 'Soft portrait', version: 2, updated_at: null })
			}
		};
		return { controller: createGenerateController(deps), start, stack: () => stack };
	}

	it('after a resize starts a run at once with the same area, and the step keeps its place', async () => {
		const t = build(stackWith(resize(500), resize(250)));
		await t.controller.prepare('edit_region');
		const outcome = await t.controller.redo('s2');
		expect(outcome).toEqual({ kind: 'started' });
		expect(t.start).toHaveBeenCalledTimes(1);
		const body = (t.start.mock.calls[0] as unknown[])[1] as { region: unknown; seed: number | null };
		expect(body.region).toEqual({ item_id: 'mask1' });
		expect(body.seed).toBe(2);
		const next = readGenerateParams(t.stack().steps[1].params)!;
		expect(next.results).toEqual([]);
		expect(t.stack().steps).toHaveLength(2);
	});

	it('after a crop asks for the area in the prompt, and Generate waits until one is marked', async () => {
		const t = build(stackWith(crop(0), crop(0.25)));
		t.controller.use('edit_region');
		await t.controller.prepare('edit_region');
		const outcome = await t.controller.redo('s2');
		expect(outcome).toEqual({ kind: 'area', task: 'edit_region' });
		expect(t.start).not.toHaveBeenCalled();
		const view = mountComponent<Record<string, unknown>>(GenerateFixture, { controller: t.controller });
		await settle();
		const q = <E extends Element = HTMLElement>(selector: string) => view.target.querySelector<E>(selector);
		const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
		expect(text('[data-prompt-status="redo"]')).toBe('Mark the area again');
		expect(text('[data-prompt-redo]')).toBe('The picture beneath changed shape');
		expect(q<HTMLTextAreaElement>('[data-prompt-field]')!.value).toBe('red sailboat');
		expect(q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(true);
		maskArea.set({ width: 200, height: 150 });
		await settle();
		expect(q('[data-prompt-status="redo"]')).toBeNull();
		expect(q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(false);
		q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.click();
		await settle();
		expect(t.start).toHaveBeenCalledTimes(1);
		expect(t.stack().steps).toHaveLength(2);
		expect(get(t.controller.ui).redo).toBeNull();
		view.destroy();
	});
});
