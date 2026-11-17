import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get, writable } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import type { PresetInfo } from '$lib/types/api';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { createRunEngine, runViews } from '$lib/tool-tabs/image/generate/runEngine';
import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
import { presetPickerState } from '$lib/stores/presetPickerState';
import { viewportWidth } from '$lib/stores/viewport';
import GenerateFixture from './GenerateFixture.svelte';

vi.mock('$lib/utils/presetHardware', async (orig) => ({
	...(await orig<typeof import('$lib/utils/presetHardware')>()),
	fetchDetectedVramGb: vi.fn().mockResolvedValue(16)
}));

const { api } = await import('$lib/services/api/index');
const { activeConfirm, settleConfirm } = await import('$lib/stores/confirm');

Element.prototype.scrollIntoView = () => {};

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({
	preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over
});
const flux = (over: Partial<TaskOffer> = {}) => offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'editor:fill', from_mode: 'inpaint', mode_label: 'Fill', region_strategy: 'crop', ...over });
const edit = (over: Partial<EditorTaskInfo> = {}): EditorTaskInfo => ({ id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer(), flux()], default: null, ...over });
const quiet = (over: Partial<EditorTaskInfo> = {}) => edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } }, ...over });
const run: EditorRun = { id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [] };
const stored = (id: string, name: string, mode: string, steps: number) => ({ id, name, updated_at: '2026-11-14T10:00:00Z', created_at: '2026-11-10T10:00:00Z', pinned: false, version: 3, data: { [mode]: { formData: { steps } } } });
const schema = { properties: { main: { children: [{ name: 'steps', type: 'number', default: 28 }] } } };
const catalog = (): PresetInfo[] => [
	{ id: 'qwen', name: 'Qwen-Image 2.1', version: '2.1', tags: [], category: 'qwen', engine: 'native', engine_available: true },
	{ id: 'flux', name: 'Flux Fill', version: '1.0', tags: [], category: 'flux', engine: 'native', engine_available: true },
	{ id: 'sdxl', name: 'SDXL', version: '1.4', tags: [], category: 'sdxl', engine: 'native', engine_available: true }
];

async function settle(rounds = 40) {
	for (let i = 0; i < rounds; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

interface Options {
	sessions?: Record<string, unknown[]>;
	formulas?: Array<{ id: string; name: string; values: Record<string, unknown> }>;
}

function build(tasks: EditorTaskInfo[], options: Options = {}) {
	let stack: EditStackState<unknown> = createStack();
	const start = vi.fn(async () => run);
	const engine = createRunEngine(
		{ start, get: async () => run, cancel: async () => run, socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const ports = {
		tasks: vi.fn(async () => tasks),
		sessions: vi.fn(async (presetId: string) => (options.sessions?.[presetId] ?? []) as never),
		formulas: vi.fn(async () => options.formulas ?? []),
		schema: vi.fn(async () => schema),
		presets: vi.fn(async () => catalog()),
		setDefault: vi.fn(async () => undefined),
		clearDefault: vi.fn(async () => undefined),
		saveSession: vi.fn(async () => ({ id: 'new', name: 'Mine', version: 1, updated_at: null })),
		updateSession: vi.fn(async () => ({ id: 's1', name: 'Soft portrait', version: 4, updated_at: null })),
		deleteSession: vi.fn(async () => undefined),
		pinSession: vi.fn(async () => undefined),
		versions: vi.fn(async () => []),
		version: vi.fn(async () => null)
	};
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed', revision: 1 }),
		landRegion: async () => ({ item_id: 'mask' }),
		areaSize: () => ({ width: 412, height: 380 }),
		stepsBeneath: async () => [],
		engine,
		ports
	};
	return { controller: createGenerateController(deps), ports, start };
}

async function open(tasks: EditorTaskInfo[], options: Options = {}, props: Record<string, unknown> = {}) {
	const built = build(tasks, options);
	built.controller.use('edit_region');
	const view = mountComponent<Record<string, unknown>>(GenerateFixture, { controller: built.controller, ...props });
	await settle();
	const q = <T extends Element = HTMLElement>(selector: string) => document.body.querySelector<T>(selector);
	const all = <T extends Element = HTMLElement>(selector: string) => Array.from(document.body.querySelectorAll<T>(selector));
	const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
	return { view, q, all, text, ...built };
}

async function click(element: Element | null | undefined) {
	(element as HTMLElement).click();
	await settle(6);
}

const modeTrigger = () => document.body.querySelector<HTMLButtonElement>('[data-testid="preset-header-mode"] button');
const optionLabels = () => Array.from(document.body.querySelectorAll('[role="option"]')).map((node) => (node.textContent ?? '').replace(/\s+/g, ' ').trim());

let pushRecent: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	markEditorExtensionsReady();
	mediaToolCatalogRevision.set(1);
	maskArea.set(null);
	viewportWidth.set(1440);
	presetPickerState.reset();
	vi.spyOn(api, 'getPresetPickerState').mockResolvedValue({ success: true, data: { pinned: ['flux'], recent: ['qwen'] } } as never);
	pushRecent = vi.spyOn(api, 'pushRecentPreset').mockResolvedValue({ success: true, data: { pinned: [], recent: [] } } as never);
	vi.spyOn(api, 'getPresetFormSchema').mockResolvedValue({ success: true, data: { preset_id: 'qwen', form_schema: schema } } as never);
});

afterEach(() => {
	vi.restoreAllMocks();
	maskArea.set(null);
	runViews.set({});
	document.body.innerHTML = '';
});

describe('the preset picker', () => {
	it('lists only the presets that have an editor mode for this task, with Recent and Pinned from the Generate page', async () => {
		const t = await open([quiet()]);
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		expect(t.all('[data-preset-row]').map((row) => row.getAttribute('data-preset-row'))).toEqual(expect.arrayContaining(['qwen', 'flux']));
		expect(t.q('[data-preset-row="sdxl"]')).toBeNull();
		expect(t.all('[data-preset-group]').map((group) => group.getAttribute('data-preset-group'))).toEqual(expect.arrayContaining(['pinned']));
		expect(t.q('[data-preset-group="recent"]')).toBeNull();
	});

	it('keeps a preset whose modes cannot run in the list, disabled, with its reason', async () => {
		const t = await open([quiet({ offers: [offer(), flux({ qualifies: false, reason: 'models_missing' })] })]);
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		const row = t.q('[data-preset-row="flux"]')!;
		expect(row.getAttribute('aria-disabled')).toBe('true');
		expect(row.querySelector('[data-preset-unavailable]')?.textContent?.trim()).toBe('Models not installed');
		expect(row.querySelector<HTMLButtonElement>('button.row-main')!.disabled).toBe(true);
		await click(row.querySelector('button.row-main'));
		expect(get(t.controller.ui).choice.edit_region?.preset_id).toBe('qwen');
	});

	it('picks a preset on its first mode and does not write to the Generate page Recent', async () => {
		const t = await open([quiet({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 }), flux()] })]);
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		await click(t.q('[data-preset-row="flux"] button.row-main'));
		expect(get(t.controller.ui).choice.edit_region).toEqual({ preset_id: 'flux', mode: 'editor:fill', setup: { kind: 'defaults' } });
		expect(t.text('[data-testid="preset-picker-trigger"]')).toBe('Flux Fill');
		expect(pushRecent).not.toHaveBeenCalled();
	});

	it('shows Pinned from the shared state and lets the member pin from the editor', async () => {
		const pin = vi.spyOn(api, 'pinPreset').mockResolvedValue({ success: true, data: { pinned: ['flux', 'qwen'], recent: [] } } as never);
		const t = await open([quiet()]);
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		expect(t.q('[data-preset-group="pinned"] ~ ul [data-preset-row="flux"]')).not.toBeNull();
		await presetPickerState.togglePin('qwen');
		expect(pin).toHaveBeenCalledWith('qwen');
	});
});

describe('the mode picker', () => {
	it('is hidden for a preset with one editor mode, as on the Generate page', async () => {
		const t = await open([edit()]);
		expect(modeTrigger()).toBeNull();
	});

	it('lists the editor modes of the preset with their names and short descriptions, and switches on the defaults of the new mode', async () => {
		const t = await open([edit({ offers: [offer({ short_description: 'Change one part' }), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		expect(modeTrigger()?.textContent?.trim()).toBe('Edit');
		await click(modeTrigger());
		expect(optionLabels()).toEqual(['Edit Change one part', 'Edit 2 Repaints inside the mask']);
		await click(t.all('[role="option"]')[1]);
		expect(get(t.controller.ui).choice.edit_region).toEqual({ preset_id: 'qwen', mode: 'editor:edit2', setup: { kind: 'defaults' } });
		expect(modeTrigger()?.textContent?.trim()).toBe('Edit 2');
	});

	it('leaves out the modes that cannot run while another one can', async () => {
		const t = await open([edit({ offers: [offer(), offer({ mode: 'editor:bad', mode_label: 'Broken', qualifies: false, reason: 'models_missing' })] })]);
		expect(t.q('[data-testid="preset-header-mode"]')).toBeNull();
	});
});

describe('the session control', () => {
	const sessions = { qwen: [stored('s1', 'Soft portrait', 'editor:edit', 20), stored('s2', 'Crisp', 'editor:edit', 40), stored('s3', 'Other mode', 'editor:edit2', 5)] };

	it('lists the editor sessions of the picked mode, loads one and sends its values with the editor key', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions });
		expect(t.text('[data-testid="session-trigger"]')).toBe('No saved session');
		await click(t.q('[data-testid="session-trigger"]'));
		expect(t.all('[data-session-row]').map((row) => row.getAttribute('data-session-row')).sort()).toEqual(['s1', 's2']);
		expect(t.q('[data-session-row="s3"]')).toBeNull();
		await click(t.q('[data-session-row="s1"]'));
		expect(t.text('[data-testid="session-trigger"]')).toBe('Soft portrait');
		t.controller.setPrompt('edit_region', 'a boat');
		await t.controller.generate('edit_region', false);
		const body = (t.start.mock.calls[0] as unknown[])[1] as { setup: { preset_id: string; mode: string; form_data: Record<string, unknown> } };
		expect(body.setup).toMatchObject({ preset_id: 'qwen', mode: 'editor:edit', form_data: { steps: 20 } });
		expect(body.setup).not.toHaveProperty('variant');
	});

	it('walks preset, then mode, then session, and Generate sends exactly that setup', async () => {
		const t = await open(
			[quiet({ offers: [offer(), flux(), flux({ mode: 'editor:fill2', mode_label: 'Fill 2', order: 1 })] })],
			{ sessions: { flux: [stored('f1', 'Wide fill', 'editor:fill2', 11)] } }
		);
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		await click(t.q('[data-preset-row="flux"] button.row-main'));
		await click(modeTrigger());
		await click(t.all('[role="option"]').find((node) => node.textContent?.includes('Fill 2')));
		await click(t.q('[data-testid="session-trigger"]'));
		await click(t.q('[data-session-row="f1"]'));
		t.controller.setPrompt('edit_region', 'a boat');
		await t.controller.generate('edit_region', false);
		const body = (t.start.mock.calls[0] as unknown[])[1] as { setup: { preset_id: string; mode: string; form_data: Record<string, unknown> } };
		expect(body.setup).toMatchObject({ preset_id: 'flux', mode: 'editor:fill2', form_data: { steps: 11 } });
	});

	it('saves to the selected session through the control and clears the Changed mark', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions });
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 12 });
		await settle();
		expect(t.text('[data-setup-changed]')).toBe('Changed');
		await click(t.q('[data-testid="session-save"]'));
		expect(t.ports.updateSession).toHaveBeenCalledWith('s1', expect.objectContaining({ mode: 'editor:edit', kind: 'editor', base_version: 3, data: { 'editor:edit': { formData: { steps: 12 } } } }));
		expect(t.q('[data-setup-changed]')).toBeNull();
	});

	it('saves the model defaults as a new session through the control, asking for a name', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions });
		await click(t.q('[data-testid="session-save"]'));
		const name = t.q<HTMLInputElement>('#session-save-name')!;
		expect(name).not.toBeNull();
		name.value = 'Crisp';
		name.dispatchEvent(new Event('input', { bubbles: true }));
		await settle(6);
		const confirm = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find((button) => /^Save/.test(button.textContent?.trim() ?? ''))!;
		await click(confirm);
		expect(t.ports.saveSession).not.toHaveBeenCalled();
		expect(t.q('#session-save-name')?.closest('[role="dialog"]')?.textContent).toContain('already');
		name.value = 'Brand new';
		name.dispatchEvent(new Event('input', { bubbles: true }));
		await settle(6);
		await click(Array.from(document.body.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')).find((button) => /^Save/.test(button.textContent?.trim() ?? '')));
		expect(t.ports.saveSession).toHaveBeenCalledWith(expect.objectContaining({ preset_id: 'qwen', name: 'Brand new', mode: 'editor:edit', kind: 'editor' }));
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 'new' });
	});

	it('offers no auto-save, which the editor does not have', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions });
		await click(t.q('[data-testid="session-trigger"]'));
		expect(document.body.textContent).not.toContain('Auto-save');
		expect(document.body.textContent).toContain('New session');
	});

	it('asks before replacing unsaved changes when another session is picked, and keeps them on Keep editing', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions });
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 12 });
		await settle();
		await click(t.q('[data-testid="session-trigger"]'));
		await click(t.q('[data-session-row="s2"]'));
		expect(document.body.textContent).toContain('has changes that are not saved');
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 's1' });
	});

	it('shows one extra Formula control only when the mode has formulas and applies one as the source', async () => {
		const none = await open([edit({ offers: [offer()] })]);
		expect(none.q('[data-testid="editor-formula"]')).toBeNull();
		none.view.destroy();
		const t = await open([edit({ offers: [offer()] })], { formulas: [{ id: 'f1', name: 'Film look', values: { steps: 40 } }] });
		expect(t.ports.formulas).toHaveBeenCalledWith('qwen', 'editor:edit');
		await click(t.q('[data-testid="editor-formula"] button'));
		await click(t.all('[role="option"]').find((node) => node.textContent?.includes('Film look')));
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'formula', id: 'f1' });
		expect(t.text('[data-testid="editor-formula"]')).toContain('Film look');
		await click(t.q('[data-testid="editor-formula"] button'));
		await click(t.all('[role="option"]').find((node) => node.textContent?.includes('No formula')));
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'defaults' });
	});
});

describe('switching with unsaved changes', () => {
	it('asks before leaving the setup for another mode, keeps it on Keep editing and switches on Discard', async () => {
		const t = await open([edit({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })]);
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 28 });
		t.controller.tweak('edit_region', { steps: 12 });
		await settle();
		await click(modeTrigger());
		await click(t.all('[role="option"]')[1]);
		const request = get(activeConfirm)!;
		expect(request.title).toBe('Discard your changes?');
		expect(request.message).toBe('Your changes to “Model defaults” are used only for this edit unless you save them.');
		expect(request.cancelLabel).toBe('Keep editing');
		settleConfirm(request.id, false);
		await settle();
		expect(get(t.controller.ui).choice.edit_region?.mode).toBe('editor:edit');
		await click(modeTrigger());
		await click(t.all('[role="option"]')[1]);
		settleConfirm(get(activeConfirm)!.id, true);
		await settle();
		expect(get(t.controller.ui).choice.edit_region?.mode).toBe('editor:edit2');
		expect(t.controller.changedFor('edit_region')).toBe(false);
	});

	it('asks the same way before another preset replaces the changed setup', async () => {
		const t = await open([edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })]);
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 28 });
		t.controller.tweak('edit_region', { steps: 12 });
		await settle();
		await click(t.q('[data-testid="preset-picker-trigger"]'));
		await click(t.q('[data-preset-row="flux"] button.row-main'));
		expect(get(activeConfirm)?.title).toBe('Discard your changes?');
		settleConfirm(get(activeConfirm)!.id, false);
		await settle();
		expect(get(t.controller.ui).choice.edit_region?.preset_id).toBe('qwen');
	});
});

describe('use as my default', () => {
	it('stores the current preset, editor mode and source when switched on, and clears it when switched off', async () => {
		const t = await open([edit({ offers: [offer()] })], { sessions: { qwen: [stored('s1', 'Soft portrait', 'editor:edit', 20)] } });
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		await settle();
		const toggle = () => t.q<HTMLInputElement>('[data-picker-default] [role="switch"]')!;
		expect(toggle().checked).toBe(false);
		await click(toggle());
		expect(t.ports.setDefault).toHaveBeenCalledWith('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		expect(toggle().checked).toBe(true);
		await click(toggle());
		expect(t.ports.clearDefault).toHaveBeenCalledWith('edit_region');
		expect(toggle().checked).toBe(false);
	});

	it('is on when the setup in use is the default and off for any other', async () => {
		const t = await open([edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } })]);
		expect(t.q<HTMLInputElement>('[data-picker-default] [role="switch"]')?.checked).toBe(true);
		await t.controller.pick('edit_region', { preset_id: 'flux', mode: 'editor:fill', setup: { kind: 'defaults' } });
		await settle();
		expect(t.q<HTMLInputElement>('[data-picker-default] [role="switch"]')?.checked).toBe(false);
	});
});

describe('first use', () => {
	it('opens the preset dropdown, says what to do and keeps Generate off until a preset is picked', async () => {
		const t = await open([edit()]);
		expect(t.q('[data-preset-row]')).not.toBeNull();
		expect(get(t.controller.ui).picker.open).toBe(false);
		expect(t.q('[data-picker-hint]')).toBeNull();
		expect(t.all('[data-dock-row]').map((row) => row.getAttribute('data-dock-row'))).toEqual(['strip', 'prompt']);
		expect(t.q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(true);
		expect(t.q('[data-prompt-mark] button')?.getAttribute('aria-label')).toBe('Choose a preset first');
		expect(t.text('[data-testid="preset-picker-trigger"]')).toBe('Choose a preset');
		await click(t.q('[data-preset-row="qwen"] button.row-main'));
		expect(t.q('[data-picker-hint]')).toBeNull();
		t.controller.setPrompt('edit_region', 'a boat');
		await settle();
		expect(t.q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(false);
	});

	it('preselects silently when one preset qualifies, with no dropdown and no hint', async () => {
		const t = await open([edit({ offers: [offer(), flux({ qualifies: false, reason: 'models_missing' })] })]);
		expect(t.q('[data-preset-row]')).toBeNull();
		expect(t.q('[data-picker-hint]')).toBeNull();
		expect(t.text('[data-testid="preset-picker-trigger"]')).toBe('Qwen-Image 2.1');
	});

	it('names what went away for a broken default above the pickers and substitutes nothing', async () => {
		const broken = edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 'gone' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } } });
		const t = await open([broken]);
		expect(t.text('[data-picker-notice]')).toBe('Your default setup "Soft portrait" was deleted. Pick another.');
		expect(t.q('[data-picker-notice]')?.getAttribute('role')).toBe('alert');
		expect(t.q('[data-dock-row="temp"] [data-picker-notice]')).not.toBeNull();
		expect(t.q('[data-preset-row]')).toBeNull();
		expect(t.text('[data-testid="preset-picker-trigger"]')).toBe('Choose a preset');
		expect(t.q<HTMLButtonElement>('[data-prompt-generate], [data-prompt-mark] button')!.disabled).toBe(true);
	});

	it('says no model can do the task when nothing is offered and disables the preset control', async () => {
		const t = await open([edit({ offers: [] })]);
		expect(t.text('[data-picker-empty]')).toContain('No model can do this yet');
		expect(t.text('[data-picker-empty]')).toContain('Ask your admin to add a model that edits pictures.');
		expect(t.q<HTMLButtonElement>('[data-testid="preset-picker-trigger"]')!.disabled).toBe(true);
		expect(t.q('[data-picker-hint]')).toBeNull();
	});
});

describe('on the phone', () => {
	it('stacks the same three controls in the prompt sheet, with the session control in its sheet layout', async () => {
		const t = await open(
			[edit({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 })] })],
			{ sessions: { qwen: [stored('s1', 'Soft portrait', 'editor:edit', 20)] } },
			{ phone: true }
		);
		viewportWidth.set(390);
		await settle();
		expect(t.q('[data-floating-prompt="phone"]')).not.toBeNull();
		const pickers = t.q('[data-editor-pickers="phone"]')!;
		expect(pickers.querySelector('[data-testid="preset-picker-trigger"]')).not.toBeNull();
		expect(pickers.querySelector('[data-testid="preset-header-mode"]')).not.toBeNull();
		expect(pickers.querySelector('[data-testid="session-trigger"]')).toBeNull();
		expect(pickers.querySelector('button[aria-label="Session"]')).not.toBeNull();
		expect(pickers.querySelector('[data-testid="session-save"]')).not.toBeNull();
		await click(pickers.querySelector('button[aria-label="Session"]'));
		expect(t.q('[data-session-row="s1"]')).not.toBeNull();
	});

	it('opens the preset list on first use, full screen, with the hint above the controls', async () => {
		viewportWidth.set(390);
		const t = await open([edit()], {}, { phone: true });
		await settle();
		expect(t.q('[role="dialog"][aria-label="Choose preset"]')).not.toBeNull();
		expect(new Set(t.all('[data-preset-row]').map((row) => row.getAttribute('data-preset-row'))).size).toBe(2);
		expect(t.text('[data-editor-pickers="phone"] [data-picker-hint]')).toBe('Choose a preset first');
		await click(t.q('[data-preset-row="flux"] button.row-main'));
		expect(get(t.controller.ui).choice.edit_region?.preset_id).toBe('flux');
		expect(pushRecent).not.toHaveBeenCalled();
	});
});
