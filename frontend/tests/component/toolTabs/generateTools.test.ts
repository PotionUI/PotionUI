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

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({ preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'edit', mode_label: 'Edit', label: null, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, owned: [], ...over });
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
			sessions: async () => (saved ? [{ id: 's1', name: 'Soft portrait', updated_at: null, data: { edit: { formData: {} } } }] : []),
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
		expect(text('[data-setup-chip]')).toBe('Qwen-Image 2.1 · Edit · Model defaults');
		expect(text('[data-prompt-status]')).toBe('Whole picture');
		expect(q<HTMLTextAreaElement>('[data-prompt-field]')?.placeholder).toBe('Describe the change');
		const generate = () => q<HTMLButtonElement>('[data-prompt-generate]')!;
		expect(generate().disabled).toBe(true);
		const field = q<HTMLTextAreaElement>('[data-prompt-field]')!;
		field.value = 'a small red sailboat';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(generate().disabled).toBe(false);
		maskArea.set({ width: 412, height: 380 });
		await settle();
		expect(text('[data-prompt-status]')).toBe('Marked area412×380');
		expect(text('[data-floating-prompt]')).toContain('Esc');
		expect(text('[data-floating-prompt]')).toContain('Ctrl');
		view.destroy();
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
		expect(text('[data-prompt-status]')).toBe('Mark the object first');
		expect(q<HTMLButtonElement>('[data-prompt-generate]')!.disabled).toBe(true);
		maskArea.set({ width: 100, height: 80 });
		await settle();
		expect(q<HTMLButtonElement>('[data-prompt-generate]')!.disabled).toBe(false);
	});

	it('swaps Generate for Cancel while a run is going and cancels the step', async () => {
		const t = build([edit()]);
		t.controller.use('edit_region');
		const cancel = vi.spyOn(t.controller, 'cancel');
		const { q } = mountPrompt(t.controller, { running: 's1' });
		await settle();
		expect(q('[data-prompt-generate]')).toBeNull();
		q<HTMLButtonElement>('[data-prompt-cancel]')!.click();
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
		q<HTMLButtonElement>('[data-prompt-generate]')!.click();
		await settle();
		expect(t.start).not.toHaveBeenCalled();
		expect(q<HTMLButtonElement>('[data-prompt-generate]')!.disabled).toBe(false);
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

describe('setup picker', () => {
	const two = () => edit({ offers: [offer(), offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'inpaint', mode_label: 'Inpaint', region_strategy: 'crop' })] });

	it('opens on first use with every mode, a choice and the default switch', async () => {
		const t = build([two()], true);
		t.controller.use('edit_region');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(text('[data-setup-chip]')).toBe('Choose a setup');
		expect(text('[data-setup-picker]')).toContain('Setup for Edit a region');
		expect(text('[data-setup-picker]')).toContain('Repaints inside the mask');
		expect(text('[data-setup-picker]')).toContain('Works on the marked area');
		expect(document.body.querySelectorAll('[data-picker-option="defaults"]')).toHaveLength(2);
		expect(text('[data-picker-option="session"]')).toContain('Soft portrait');
		expect(q<HTMLButtonElement>('[data-picker-use]')!.disabled).toBe(true);
		q<HTMLButtonElement>('[data-picker-option="session"]')!.click();
		await settle();
		expect(q('[data-picker-option="session"]')?.getAttribute('aria-checked')).toBe('true');
		q<HTMLButtonElement>('[data-picker-use]')!.click();
		await settle();
		expect(q('[data-setup-picker]')).toBeNull();
		expect(text('[data-setup-chip]')).toBe('Qwen-Image 2.1 · Edit · Soft portrait');
	});

	it('saves the default only when the switch is on', async () => {
		const t = build([two()]);
		t.controller.use('edit_region');
		const { q } = mountPrompt(t.controller);
		await settle();
		q<HTMLButtonElement>('[data-picker-option="defaults"]')!.click();
		q<HTMLButtonElement>('[role="switch"]')!.click();
		await settle();
		q<HTMLButtonElement>('[data-picker-use]')!.click();
		await settle();
		expect(t.deps.ports.setDefault).toHaveBeenCalledWith('edit_region', expect.objectContaining({ preset_id: 'qwen', mode: 'edit', setup: { kind: 'defaults' } }));
	});

	it('names what went away when the default broke and never picks another for the member', async () => {
		const broken = two();
		broken.default = { preset_id: 'qwen', mode: 'edit', variant: null, setup: { kind: 'session', id: 'gone' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } };
		const t = build([broken]);
		t.controller.use('edit_region');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(text('[data-picker-notice]')).toBe('Your default setup "Soft portrait" was deleted. Pick another.');
		expect(q('[data-picker-notice]')?.getAttribute('role')).toBe('alert');
		expect(text('[data-setup-chip]')).toBe('Choose a setup');
		expect(q<HTMLButtonElement>('[data-prompt-generate]')!.disabled).toBe(true);
	});

	it('greys the modes with their reasons when none can run, and keeps Use off', async () => {
		const t = build([edit({ offers: [offer({ qualifies: false, reason: 'models_missing' }), offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode_label: 'Inpaint', qualifies: false, reason: 'not_assigned' })] })]);
		t.controller.use('edit_region');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(text('[data-setup-picker]')).toContain('Models not installed');
		expect(text('[data-setup-picker]')).toContain('Not available to your account');
		expect(document.body.querySelectorAll('[data-picker-option]:disabled')).toHaveLength(2);
		expect(q<HTMLButtonElement>('[data-picker-use]')!.disabled).toBe(true);
		expect(text('[data-setup-chip]')).toBe('No model yet');
	});

	it('shows the empty state when no model offers the task', async () => {
		const t = build([edit({ offers: [] })]);
		t.controller.use('edit_region');
		const { q, text } = mountPrompt(t.controller);
		await settle();
		expect(q('[data-picker-empty]')).not.toBeNull();
		expect(text('[data-picker-empty]')).toContain('No model can do this yet');
		expect(text('[data-picker-empty]')).toContain('Ask your admin to add a model that edits pictures.');
		expect(q('[data-picker-use]')).toBeNull();
	});
});

describe('stack rows of a generate step', () => {
	function stackOf(params: GenerateParams): EditStackState<unknown> {
		return apply(beginEdit(createStack(), generateDraft(params, 'Edit a region', 'sparkles')));
	}
	const base = (over: Partial<GenerateParams> = {}): GenerateParams => ({
		v: 1, task: 'edit_region', prompt: 'red sailboat', candidates: 3, results: [], chosen: 0, seed: null, rect: null, soft: false,
		setup: { preset_id: 'qwen', mode: 'edit', variant: null, form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
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
		expect(text('[data-setup-chip]')).toBe('Qwen-Image 2.1 · Edit · Model defaults');
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
		view.destroy();
	});
});
