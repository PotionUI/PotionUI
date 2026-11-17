import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { apply, beginEdit, createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
import { generateDraft, withRun, type GenerateParams } from '$lib/tool-tabs/image/generate/generateStep';
import { createRunEngine, runViews } from '$lib/tool-tabs/image/generate/runEngine';
import { imageRowInfo } from '$lib/tool-tabs/image/stepKinds';
import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
import { api } from '$lib/services/api/index';
import { syncServerTasks } from '$lib/tool-tabs/image/generate/register';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import GenerateFixture from './GenerateFixture.svelte';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const { default: EditStack } = await import('$lib/components/tool-tabs/frame/EditStack.svelte');

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({ preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over });
const edit = (over: Partial<EditorTaskInfo> = {}): EditorTaskInfo => ({ id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer()], default: null, ...over });
const remove = (): EditorTaskInfo => edit({ id: 'remove_object', label: 'Remove an object', icon: 'mode-remove-object', region: 'required', prompt: 'none' });
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });

async function settle() {
	for (let i = 0; i < 60; i++) await Promise.resolve();
	flushSync();
}

function build(tasks: EditorTaskInfo[], saved = false) {
	let stack: EditStackState<unknown> = createStack();
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
		landRegion: async () => ({ item_id: 'mask' }),
		areaSize: () => ({ width: 412, height: 380 }),
		stepsBeneath: async () => [],
		engine,
		ports: {
			tasks: async () => tasks,
			sessions: async () => (saved ? [{ id: 's1', name: 'Soft portrait', updated_at: null, data: { 'editor:edit': { formData: {} } } }] : []),
			formulas: async () => [],
			schema: async () => ({ properties: {} }),
			setDefault: vi.fn(async () => undefined),
			clearDefault: async () => undefined,
			saveSession: vi.fn(async () => ({ id: 'new', name: 'New', version: 1, updated_at: null })),
			updateSession: vi.fn(async () => ({ id: 's1', name: 'Soft portrait', version: 2, updated_at: null }))
		}
	};
	return { controller: createGenerateController(deps), deps, start, stack: () => stack };
}

function mountPrompt(controller: ReturnType<typeof build>['controller'], props: Record<string, unknown> = {}) {
	const view = mountComponent<Record<string, unknown>>(GenerateFixture, { controller, ...props });
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
	return { view, q, text };
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

describe('floating prompt', () => {
	it('walks from first use to a ready Generate for Edit a region', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q, text, view } = mountPrompt(t.controller);
		await settle();
		expect(text('[data-testid="preset-picker-trigger"]')).toBe('Qwen-Image 2.1');
		expect(text('[data-testid="session-trigger"]')).toBe('No saved session');
		expect(q('[data-picker-hint]')).toBeNull();
		expect(q<HTMLTextAreaElement>('[data-prompt-field]')?.placeholder).toBe('Describe the change');
		const generate = () => q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!;
		expect(generate().disabled).toBe(true);
		const field = q<HTMLTextAreaElement>('[data-prompt-field]')!;
		field.value = 'a small red sailboat';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(generate().disabled).toBe(false);
		maskArea.set({ width: 412, height: 380 });
		await settle();
		expect(text('[data-floating-prompt]')).not.toContain('Esc');
		expect(q('[data-prompt-grip]')).toBeNull();
		expect(q('[data-prompt-mark]')).not.toBeNull();
		view.destroy();
	});

	it('lays the dock out as variant A: the picker strip with labels, shared cuts, default and Settings at its end, then the prompt row', async () => {
		const t = build([edit({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller, { settings: { open: false, toggle: () => undefined } });
		await settle();
		const dock = q('[data-floating-prompt="desktop"]')!;
		const rows = Array.from(dock.querySelectorAll('[data-dock-row]')).map((row) => row.getAttribute('data-dock-row'));
		expect(rows).toEqual(['strip', 'prompt']);
		const strip = dock.querySelector('[data-dock-row="strip"]')!;
		expect(strip.className).toContain('border-b');
		const zone = strip.querySelector('[data-testid="context-zone"]')!;
		expect(zone.className).toContain('h-12');
		expect(Array.from(zone.querySelectorAll('.ctx-label')).map((label) => label.textContent?.trim())).toEqual(['Preset', 'Mode', 'Session', 'Results']);
		expect(zone.querySelectorAll('[data-testid="context-separator"]')).toHaveLength(2);
		expect(strip.querySelector('[data-editor-pickers-end] [data-picker-default]')).not.toBeNull();
		expect(strip.querySelector('[data-editor-pickers-end] [data-prompt-settings]')).not.toBeNull();
		expect(strip.querySelector('[data-testid="session-save"]')?.textContent?.trim()).toBe('');
		const second = q('[data-dock-row="prompt"]')!;
		expect(second.className).toContain('pb-3');
		expect(second.querySelector('[aria-label="Results"]')).toBeNull();
		expect(second.querySelector('[data-prompt-mark]')).toBeNull();
		expect(second.querySelector('.ai-ring [data-prompt-field]')).not.toBeNull();
		const end = strip.querySelector('[data-editor-pickers-end]')!;
		const order = Array.from(end.children).map((child) => (child.querySelector('[aria-label="Results"]') ? 'results' : child.querySelector('[data-picker-default]') || child.hasAttribute('data-picker-default') ? 'default' : child.querySelector('[data-prompt-settings]') || child.hasAttribute('data-prompt-settings') ? 'settings' : 'other'));
		expect(order.filter((entry) => entry !== 'other')).toEqual(['results', 'default', 'settings']);
		expect(end.querySelector('[data-testid="dock-results"] .ctx-label')?.textContent?.trim()).toBe('Results');
		const mark = q('[data-dock-mark]')!;
		expect(mark.className).toContain('px-3');
		expect(mark.className).toContain('border-l');
		expect(mark.className).toContain('items-center');
		expect(dock.className).toContain('grid-cols-[minmax(0,1fr)_auto]');
		expect(mark.querySelector('[data-prompt-mark] button.generate-button')).not.toBeNull();
		expect(mark.querySelector<HTMLElement>('[data-prompt-mark]')!.getAttribute('style')).toBeNull();
		expect(dock.contains(mark)).toBe(true);
		expect(strip.contains(mark)).toBe(false);
		expect(dock.className).toContain('bg-surface-1');
	});

	it('gives every strip item the same two-line cell: a label line over a 24px control line, switch and Settings on the control line', async () => {
		const t = build([edit({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller, { settings: { open: false, toggle: () => undefined } });
		await settle();
		const zone = q('[data-testid="context-zone"]')!;
		expect(zone.className).toContain('dock-zone');
		const cells = Array.from(zone.querySelectorAll('.ctx-step'));
		expect(cells).toHaveLength(4);
		for (const cell of cells) {
			expect(cell.querySelectorAll(':scope > .ctx-label')).toHaveLength(1);
			expect(cell.children).toHaveLength(2);
			expect(cell.children[0].classList.contains('ctx-label')).toBe(true);
		}
		const end = q('[data-editor-pickers-end]')!;
		const controls = Array.from(end.querySelectorAll('.dock-ctl'));
		expect(controls.map((control) => (control.hasAttribute('data-picker-default') ? 'default' : control.querySelector('[data-prompt-settings]') ? 'settings' : 'other'))).toEqual(['default', 'settings']);
		for (const control of controls) expect(control.querySelector('.ctx-label')).toBeNull();
		const seg = end.querySelectorAll('[data-testid="dock-results"] [aria-label="Results"] button');
		expect(seg).toHaveLength(4);
		expect(seg[0].className).toContain('h-[18px]');
		expect(end.querySelector('[data-testid="dock-results"] > span:last-child')!.className).toContain('h-6');
		expect(q('[data-prompt-settings]')!.className).toContain('min-w-6');
		expect(q('[data-testid="session-save"]')!.className).toContain('h-6');
	});

	it('shows the mode icon beside the mode name as the Generate page does, and none when the server sent none', async () => {
		const withIcon = build([edit({ offers: [offer({ mode_icon: 'mode-restyle' }), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', mode_icon: 'mode-extend', order: 1 })] })]);
		withIcon.controller.use('edit_region');
		const first = mountPrompt(withIcon.controller);
		await settle();
		const trigger = first.q('[data-testid="preset-header-mode"] button')!;
		expect(trigger.querySelectorAll('.ctx-valuerow svg')).toHaveLength(1);
		expect(trigger.querySelector('.ctx-valuerow svg')!.getAttribute('class')).toContain('text-fg-muted');
		first.view.destroy();
		const without = build([edit({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		without.controller.use('edit_region');
		const second = mountPrompt(without.controller);
		await settle();
		expect(second.q('[data-testid="preset-header-mode"] button .ctx-valuerow svg')).toBeNull();
	});

	it('draws the run along the dock top border, keeps two rows and puts the status in the mark tooltip', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q, view } = mountPrompt(t.controller, { running: 's1', run: { status: 'running', progress: 0.4 } });
		await settle();
		const bar = q('[data-floating-prompt="desktop"] > [role="progressbar"]')!;
		expect(bar.getAttribute('aria-valuenow')).toBe('40');
		expect(bar.hidden).toBe(false);
		expect(q('[data-run-chip]')).toBeNull();
		expect(q('[data-floating-prompt="desktop"]')!.className).toContain('relative');
		expect(q('[data-dock-row="temp"]')).toBeNull();
		expect(q('[data-prompt-mark] button')!.getAttribute('aria-label')).toBe('Cancel generation');
		view.destroy();
		const idle = mountPrompt(t.controller, { run: { status: 'done', progress: 1 } });
		await settle();
		expect(idle.q<HTMLElement>('[role="progressbar"]')!.hidden).toBe(true);
	});

	it('clips the cuts to the strip, pads the dock evenly and keeps the right group out of the way of long names', async () => {
		const t = build([edit({ offers: [offer({ preset_name: 'MiniMax-H3-Image' }), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		const strip = q('[data-dock-row="strip"]')!;
		expect(strip.className).toContain('overflow-hidden');
		expect(strip.className).toContain('container-type:inline-size');
		expect(q('[data-testid="context-zone"]')!.className).toContain('h-12');
		const prompt = q('[data-dock-row="prompt"]')!;
		for (const side of ['px-3', 'pt-3', 'pb-3']) expect(prompt.className).toContain(side);
		expect(strip.className).toContain('px-3');
		const steps = Array.from(strip.querySelectorAll<HTMLElement>('.ctx-step')).filter((cell) => !cell.closest('[data-editor-pickers-end]'));
		for (const cell of steps) {
			expect(cell.className).toContain('shrink');
			expect(cell.className).not.toContain('shrink-0');
			expect(cell.className).toContain('min-w-0');
		}
		expect(q('[data-preset-value]')!.className).toContain('truncate');
		expect(q('[data-testid="session-trigger"] .truncate')).not.toBeNull();
		expect(q('[data-editor-pickers-end]')!.className).toContain('flex-none');
		expect(q('[data-editor-pickers-end]')!.className).toContain('ml-auto');
	});

	it('uses the same cut as the Generate toolbar, drawn in the canvas colour on the dock surface', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		const cut = q('[data-dock-row="strip"] [data-testid="context-separator"]')!;
		expect(cut.classList.contains('context-cut')).toBe(true);
		expect(q<HTMLElement>('[data-testid="context-zone"]')!.getAttribute('style')).toContain('--cut-stroke: var(--canvas)');
	});

	it('takes extra items at the right of the strip, before the default switch', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller, { withExtras: true });
		await settle();
		const end = q('[data-editor-pickers-end]')!;
		const kids = Array.from(end.children);
		expect(kids[0].hasAttribute('data-test-extra')).toBe(true);
		expect(kids.findIndex((child) => child.querySelector('[data-picker-default]') || child.hasAttribute('data-picker-default'))).toBeGreaterThan(0);
	});

	it('puts the notices row between the strip and the prompt, and none at rest', async () => {
		const quiet = build([edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })]);
		quiet.controller.use('edit_region');
		const first = mountPrompt(quiet.controller);
		await settle();
		expect(first.q('[data-dock-row="temp"]')).toBeNull();
		first.view.destroy();
		const t = build([edit({ offers: [offer(), offer({ preset_id: 'flux', preset_name: 'Flux', mode: 'editor:fill' })] })]);
		t.controller.use('edit_region');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		const order = Array.from(q('[data-floating-prompt="desktop"]')!.querySelectorAll('[data-dock-row]')).map((row) => row.getAttribute('data-dock-row'));
		expect(order).toEqual(['strip', 'prompt']);
		expect(q('[data-picker-hint]')).toBeNull();
		expect(text('[data-testid="preset-picker-trigger"]')).toBe('Choose a preset');
		expect(q('[data-preset-row]')).not.toBeNull();
		const mark = q<HTMLButtonElement>('[data-dock-mark] button')!;
		expect(mark.disabled).toBe(true);
		expect(mark.getAttribute('aria-label')).toBe('Choose a preset first');
		const preset = q('[data-testid="preset-picker-trigger"] [data-preset-value]')!;
		expect(preset.className).toContain('text-fg-muted');
	});

	it('opens the notices row only for things that happened: a broken default', async () => {
		const broken = edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 'gone' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } } });
		const t = build([broken]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		const order = Array.from(q('[data-floating-prompt="desktop"]')!.querySelectorAll('[data-dock-row]')).map((row) => row.getAttribute('data-dock-row'));
		expect(order).toEqual(['strip', 'temp', 'prompt']);
		expect(q('[data-dock-row="temp"] [data-picker-notice]')).not.toBeNull();
	});

	it('shows the results strip in that row with 36px thumbnails and a labelled Try again', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller, { withResults: true });
		await settle();
		const row = q('[data-dock-row="temp"]')!;
		const thumbs = Array.from(row.querySelectorAll<HTMLElement>('[data-candidate]'));
		expect(thumbs).toHaveLength(2);
		expect(thumbs[0].className).toContain('h-9');
		expect(thumbs[0].className).toContain('w-9');
		expect(row.querySelector('[data-candidate-again]')?.textContent?.trim()).toBe('Try again');
	});

	it('keeps the mark in its own cell, centred, when the results row appears, and at the Generate page size', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q, view } = mountPrompt(t.controller, { withResults: false });
		await settle();
		const before = q('[data-dock-mark]')!.className;
		view.rerender({ withResults: true });
		await settle();
		expect(q('[data-dock-row="temp"]')).not.toBeNull();
		expect(q('[data-dock-mark]')!.className).toBe(before);
	});

	it('is the same two rows for a task with no prompt, with the note in the prompt row', async () => {
		const t = build([remove()]);
		t.controller.use('remove_object');
		const { q } = mountPrompt(t.controller);
		await settle();
		const second = q('[data-dock-row="prompt"]')!;
		expect(second.querySelector('[data-prompt-note]')?.textContent?.trim()).toBe('The marked object will be removed.');
		expect(q('[data-dock-row="strip"] [aria-label="Results"]')).not.toBeNull();
		expect(q('[data-dock-mark] [data-prompt-mark] button')).not.toBeNull();
		expect(q('[data-prompt-field]')).toBeNull();
	});

	it('mounts the Generate page mark and maps the editor states onto it', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const view = mountPrompt(t.controller);
		await settle();
		const host = () => view.q('[data-prompt-mark]')!;
		const button = () => host().querySelector<HTMLButtonElement>('button.generate-button')!;
		expect(host().getAttribute('data-prompt-mark')).toBe('disabled');
		expect(button().disabled).toBe(true);
		expect(button().getAttribute('aria-label')).toBe('Describe the change first');
		t.controller.setPrompt('edit_region', 'a boat');
		await settle();
		expect(host().getAttribute('data-prompt-mark')).toBe('ready');
		expect(button().disabled).toBe(false);
		expect(button().getAttribute('aria-label')).toBe('Generate');
		expect(host().classList.contains('generate-mark-host')).toBe(true);
		button().click();
		await settle();
		await settle();
		expect(t.start).toHaveBeenCalledTimes(1);
		view.view.rerender({ running: 's1' });
		await settle();
		expect(host().getAttribute('data-prompt-mark')).toBe('running');
		expect(button().classList.contains('is-cancel')).toBe(true);
		expect(button().getAttribute('aria-label')).toBe('Cancel generation');
		const cancel = vi.spyOn(t.controller, 'cancel');
		button().click();
		expect(cancel).toHaveBeenCalledWith('s1');
	});

	it('does not close on Escape, which only closes an open picker', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		q('[data-prompt-field]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(q('[data-floating-prompt="desktop"]')).not.toBeNull();
		expect(localStorage.getItem('potionui_editor_prompt_position')).toBeNull();
	});

	it('shows the results control with 1 to 4 and remembers the choice', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		const buttons = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[aria-label="Results"] button'));
		expect(buttons.map((button) => button.textContent?.trim())).toEqual(['1', '2', '3', '4']);
		expect(buttons[0].getAttribute('aria-pressed')).toBe('true');
		buttons[2].click();
		await settle();
		expect(q('[aria-label="Results"] button[aria-pressed="true"]')?.textContent?.trim()).toBe('3');
	});

	it('starts a run with Ctrl+Enter and tells the controller about the marked area', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		maskArea.set({ width: 412, height: 380 });
		t.controller.setPrompt('edit_region', 'a boat');
		await settle();
		q('[data-prompt-field]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
		await settle();
		await settle();
		expect(t.start).toHaveBeenCalledTimes(1);
		expect(t.stack().steps).toHaveLength(1);
	});

	it('draws Remove an object without a text box, with the warning until something is marked', async () => {
		const t = build([remove()]);
		t.controller.use('remove_object');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(q('[data-prompt-field]')).toBeNull();
		expect(text('[data-prompt-note]')).toBe('The marked object will be removed.');
		expect(q('[data-prompt-status]')).toBeNull();
		expect(q('[data-dock-row="temp"]')).toBeNull();
		expect(q<HTMLButtonElement>('[data-prompt-mark] button')!.getAttribute('aria-label')).toBe('Mark the object first');
		expect(q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(true);
		maskArea.set({ width: 100, height: 80 });
		await settle();
		expect(q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(false);
	});

	it('swaps Generate for Cancel while a run is going and cancels the step', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const cancel = vi.spyOn(t.controller, 'cancel');
		const { q } = mountPrompt(t.controller, { running: 's1' });
		await settle();
		expect(q('[data-prompt-mark="ready"]')).toBeNull();
		q<HTMLButtonElement>('[data-prompt-cancel], [data-prompt-mark="running"] button')!.click();
		expect(cancel).toHaveBeenCalledWith('s1');
	});

	it('says when large areas come back softer, and only then', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const view = mountPrompt(t.controller, { large: false });
		await settle();
		expect(view.q('[data-prompt-large]')).toBeNull();
		view.view.rerender({ large: true });
		expect(view.text('[data-prompt-large]')).toBe('Large areas come back softer');
	});

	it('keeps the Settings button present but disabled until the settings view exists', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const { q, view } = mountPrompt(t.controller);
		await settle();
		expect(q<HTMLButtonElement>('[data-prompt-settings]')!.disabled).toBe(true);
		const toggle = vi.fn();
		view.rerender({ settings: { open: false, toggle } });
		await settle();
		const button = q<HTMLButtonElement>('[data-prompt-settings]')!;
		expect(button.disabled).toBe(false);
		button.click();
		expect(toggle).toHaveBeenCalled();
		view.rerender({ settings: { open: true, toggle } });
		await settle();
		expect(q('[data-prompt-settings]')?.getAttribute('aria-pressed')).toBe('true');
	});

	async function failToStart(cause: unknown) {
		const t = build([edit()]);
		t.deps.landRegion = async () => {
			throw cause;
		};
		const controller = createGenerateController(t.deps);
		controller.use('edit_region');
		const { q, text } = mountPrompt(controller);
		await settle();
		maskArea.set({ width: 100, height: 80 });
		controller.setPrompt('edit_region', 'a boat');
		await settle();
		q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.click();
		await settle();
		expect(t.start).not.toHaveBeenCalled();
		expect(q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(false);
		return text('[data-prompt-error]');
	}

	it('names an area that could not be read, logs the cause and lets the member try again', async () => {
		const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
		const cause = new Error('Could not load the image');
		const shown = await failToStart(cause);
		expect(shown).toBe('The marked area could not be read. Mark it again and try again.');
		expect(logged).toHaveBeenCalledWith(expect.any(String), cause);
		logged.mockRestore();
	});

	it('keeps the sent-wording for a true network failure', async () => {
		const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
		const shown = await failToStart({ isAxiosError: true, request: {}, message: 'Network Error' });
		expect(shown).toBe('The marked area or the picture could not be sent. Try again.');
		expect(logged).not.toHaveBeenCalled();
		logged.mockRestore();
	});

	it('shows what the server said when it refused the area', async () => {
		const shown = await failToStart({ response: { status: 403, data: { detail: { message: 'The file is larger than your plan allows' } } } });
		expect(shown).toBe('The file is larger than your plan allows');
	});

	it('shows a plain failure under the prompt and starts nothing without a saved edit', async () => {
		const t = build([edit()]);
		t.deps.editLink = async () => null;
		const controller = createGenerateController(t.deps);
		controller.use('edit_region');
		const { q, text } = mountPrompt(controller);
		await settle();
		controller.setPrompt('edit_region', 'a boat');
		await controller.generate('edit_region', false);
		await settle();
		expect(text('[data-prompt-error]')).toContain('not saved yet');
		expect(q('[data-prompt-error]')?.getAttribute('role')).toBe('alert');
	});
});

describe('stack rows of a generate step', () => {
	function stackOf(params: GenerateParams): EditStackState<unknown> {
		return apply(beginEdit(createStack(), generateDraft(params, 'Edit a region', 'sparkles')));
	}
	const base = (over: Partial<GenerateParams> = {}): GenerateParams => ({
		v: 1, task: 'edit_region', prompt: 'red sailboat', candidates: 3, results: [], chosen: 0, seed: null, rect: null, soft: false,
		setup: { preset_id: 'qwen', mode: 'editor:edit', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: null, size: null }, run: { id: 'er_1', status: 'queued', error: null }, ...over
	});

	function mountStack(params: GenerateParams) {
		ensureCoreImageEditor();
		const calls: string[] = [];
		const view = mountComponent(EditStack, {
			stack: stackOf(params),
			original: { summary: '1024×1024 · PNG' },
			rowInfo: (step: never) => imageRowInfo(step),
			onrowaction: (id: string, action: string) => calls.push(`${id}:${action}`),
			onreopen: () => undefined, onseek: () => undefined, ontogglehidden: (id: string) => calls.push(`hide:${id}`), onremove: (id: string) => calls.push(`remove:${id}`), onmove: () => undefined
		});
		const row = () => view.target.querySelector<HTMLElement>('[data-stack-row="s1"]')!;
		return { calls, row, view };
	}

	const clean = (el: Element) => (el.textContent ?? '').replace(/\s+/g, ' ').trim();
	const actions = (row: HTMLElement) => Array.from(row.querySelectorAll('[data-stack-actions] button')).map((b) => b.getAttribute('aria-label'));

	it('waiting: Waiting with only Cancel', () => {
		const { row, calls } = mountStack(base());
		expect(clean(row())).toContain('Waiting');
		expect(actions(row())).toEqual(['Cancel Edit a region']);
		row().querySelector<HTMLButtonElement>('[data-stack-action="cancel"]')!.click();
		expect(calls).toEqual(['s1:cancel']);
	});

	it('generating: blue progress text and a progress line', () => {
		runViews.set({ er_1: { id: 'er_1', stepId: 's1', status: 'running', progress: 0.4, preview: null, run: null, error: null } });
		const { row } = mountStack(base({ run: { id: 'er_1', status: 'running', error: null } }));
		expect(clean(row())).toContain('Generating 40%');
		expect(row().querySelector('[data-stack-state] .text-signal')).not.toBeNull();
		expect(row().querySelector('[data-stack-progress] i')?.getAttribute('style')).toContain('width: 40%');
	});

	it('done: thumbnail of the chosen result, prompt start and 2 of 3, with Try again, Hide and Remove', () => {
		const done = withRun(base(), run({ status: 'done', rect: { x: 0, y: 0, width: 1, height: 1 }, candidates: [1, 2, 3].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: n })) }));
		const { row, calls } = mountStack({ ...done, chosen: 1 });
		expect(clean(row())).toContain('2 of 3 · red sailboat');
		expect(row().querySelector('[data-stack-thumb]')?.innerHTML).toContain('/p2.png');
		expect(actions(row())).toEqual(['Try again Edit a region', 'Hide Edit a region', 'Remove Edit a region']);
		row().querySelector<HTMLButtonElement>('[data-stack-action="again"]')!.click();
		expect(calls).toEqual(['s1:again']);
	});

	it('failed: the reason in red with Retry and Remove, no Hide', () => {
		const { row } = mountStack(base({ run: { id: 'er_1', status: 'failed', error: 'timed out' } }));
		expect(clean(row())).toContain('Did not finish: timed out');
		expect(row().querySelector('[data-stack-state] .text-danger')).not.toBeNull();
		expect(actions(row())).toEqual(['Retry Edit a region', 'Remove Edit a region']);
	});

	it('cancelled: muted Cancelled with Retry and Remove', () => {
		const { row } = mountStack(base({ run: { id: 'er_1', status: 'cancelled', error: null } }));
		expect(clean(row())).toContain('Cancelled');
		expect(actions(row())).toEqual(['Retry Edit a region', 'Remove Edit a region']);
	});
});

describe('a task a plugin registered on the server', () => {
	it('opens the same floating prompt, labelled with its own name', async () => {
		const t = build([edit({ id: 'acme.relight', label: 'Relight', prompt: 'optional' })]);
		t.controller.use('acme.relight');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(q('[data-floating-prompt]')?.getAttribute('aria-label')).toBe('Relight');
		expect(text('[data-testid="preset-picker-trigger"]')).toBe('Qwen-Image 2.1');
	});

	afterEach(() => {
		vi.restoreAllMocks();
		syncServerTasks([]);
	});

	it('appears in the Generate group and opens the same floating prompt with no change to the editor', async () => {
		const plugin = edit({ id: 'acme.relight', label: 'Relight', icon: 'sparkles', prompt: 'optional', region: 'optional' });
		vi.spyOn(api, 'listEditorTasks').mockResolvedValue([edit(), remove(), plugin]);
		vi.spyOn(api, 'listEditorSessions').mockResolvedValue([]);
		vi.spyOn(api, 'listFormulas').mockResolvedValue({ success: true, data: [] } as never);
		const host = {
			tabId: 'tab-gen', tool: 'image', source: { path: 'outputs/a.png', relative_path: 'outputs/a.png', url: '/api/media/generations/gen-1/a.png', name: 'a.png', type: 'image', generation_file_id: '501' },
			state: null, activeTool: null, run: null, hint: null, phone: false,
			setState: () => undefined, setDirty: () => undefined, setTitle: () => undefined, setThumbnail: () => undefined, setSource: () => undefined, replaceSource: () => undefined,
			setActiveTool: () => undefined, setRun: () => undefined, clearHint: () => undefined, setActions: () => undefined, openToolTab: () => 'tab-2'
		} as unknown as ToolTabHost;
		const view = mountComponent(ImageEditorTab, { host });
		await settle();
		const buttons = () => Array.from(view.target.querySelectorAll<HTMLButtonElement>('[data-tool-rail] button[data-tool]')).map((button) => button.dataset.tool);
		expect(buttons()).toContain('edit_region');
		expect(buttons()).toContain('remove_object');
		expect(buttons()).toContain('acme.relight');
		view.target.querySelector<HTMLButtonElement>('[data-tool-rail] button[data-tool="acme.relight"]')!.click();
		await settle();
		expect(view.target.querySelector('[data-options-panel]')?.getAttribute('aria-label')).toBe('Relight options');
		expect(view.target.querySelector('[data-image-editor]')?.getAttribute('data-tool')).toBe('acme.relight');
		expect(view.target.querySelector('[data-frame-dock] [data-floating-prompt="desktop"]')).not.toBeNull();
		view.target.querySelector<HTMLButtonElement>('[data-tool-rail] button[data-tool="crop"]')?.click();
		await settle();
		expect(view.target.querySelector('[data-frame-dock]')).toBeNull();
		view.destroy();
	});
});
