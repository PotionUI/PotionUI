import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountComponent } from '../helpers/mount';
import { settle } from '../helpers/settle';
import type { EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { writable } from 'svelte/store';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '$lib/tool-tabs/image/generate/controller';
import { createRunEngine } from '$lib/tool-tabs/image/generate/runEngine';
import SettingsHost from './SettingsHost.svelte';

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { markEditorExtensionsReady } = await import('$lib/editors');
const { syncServerTasks } = await import('$lib/tool-tabs/image/generate/register');
const { ensureCoreImageEditor } = await import('$lib/tool-tabs/image/coreEditor');

registerBuiltinFieldComponents();
Element.prototype.scrollIntoView = () => {};

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({
	preset_id: 'qwen',
	preset_name: 'Qwen-Image 2.1',
	mode: 'editor:edit',
	from_mode: 'edit',
	mode_label: 'Edit',
	label: null,
	short_description: null,
	order: 0,
	region_strategy: 'mask',
	via: null,
	qualifies: true,
	reason: null,
	...over
});

const sessionSetup = { kind: 'session' as const, id: 's1' };

function edit(over: Partial<EditorTaskInfo> = {}): EditorTaskInfo {
	return {
		id: 'edit_region',
		label: 'Edit a region',
		icon: 'sparkles',
		region: 'optional',
		prompt: 'required',
		fallback: null,
		offers: [offer(), offer({ mode: 'editor:other', mode_label: 'Other', order: 1 })],
		default: { preset_id: 'qwen', mode: 'editor:edit', setup: sessionSetup, health: { ok: true, reason: null, name: null, mode_label: null } },
		...over
	};
}

const SCHEMA = {
	properties: {
		custom: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					name: 'generation',
					label: 'Generation',
					children: [
						{ name: 'strength', type: 'number', title: 'Strength', default: 0.85 },
						{ name: 'style_notes', type: 'string', title: 'Style notes', default: '' }
					]
				}
			]
		}
	}
};

const SCHEMA_WITH_QUANTITY = {
	properties: {
		custom: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					name: 'generation',
					label: 'Generation',
					children: [
						{ name: 'strength', type: 'number', title: 'Strength', default: 0.85 },
						{ type: 'section', title: 'Image', children: [{ type: 'row', children: [{ name: 'quantity', type: 'integer', title: 'Quantity', default: 1 }] }] },
						{ name: 'style_notes', type: 'string', title: 'Style notes', default: '' }
					]
				}
			]
		}
	}
};

function build(task: EditorTaskInfo, sessions: unknown[]) {
	let stack: EditStackState<unknown> = createStack();
	const engine = createRunEngine(
		{ start: async () => { throw new Error('unused'); }, get: async () => { throw new Error('unused'); }, cancel: async () => { throw new Error('unused'); }, socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) },
		{ views: writable({}) }
	);
	const ports = {
		tasks: vi.fn(async () => [task]),
		sessions: vi.fn(async () => sessions as never),
		formulas: vi.fn(async () => []),
		schema: vi.fn(async () => SCHEMA),
		setDefault: vi.fn(async () => undefined),
		clearDefault: vi.fn(async () => undefined),
		saveSession: vi.fn(async () => ({ id: 'new1', name: 'Crisp', version: 1, updated_at: null })),
		updateSession: vi.fn(async () => ({ id: 's1', name: 'Soft portrait', version: 4, updated_at: null }))
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
	return { controller: createGenerateController(deps), ports };
}

const SESSIONS = [{ id: 's1', name: 'Soft portrait', updated_at: null, version: 3, data: { 'editor:edit': { formData: { strength: 0.5 } } } }];

async function open(phone = false, task: EditorTaskInfo = edit(), sessions: unknown[] = SESSIONS, served: unknown = SCHEMA) {
	vi.spyOn(api, 'getPresetFormSchema').mockResolvedValue({ success: true, data: { preset_id: 'qwen', form_schema: served } } as never);
	const built = build(task, sessions);
	const view = mountComponent(SettingsHost, { controller: built.controller, phone });
	await settle();
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const text = (selector: string) => (q(selector)?.textContent ?? '').replace(/\s+/g, ' ').trim();
	const toggle = async () => {
		q<HTMLButtonElement>('[data-prompt-settings]')!.click();
		await settle(14);
	};
	return { view, q, text, toggle, ...built };
}

function typeInto(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	input.dispatchEvent(new Event('change', { bubbles: true }));
}

beforeEach(() => {
	clearSchemaCache();
	ensureCoreImageEditor();
	markEditorExtensionsReady();
});

afterEach(() => {
	vi.restoreAllMocks();
	syncServerTasks([]);
	document.body.innerHTML = '';
});

describe('Settings in the right panel', () => {
	it('replaces the tool options in the right panel and keeps the prompt and the canvas', async () => {
		const { view, q, text, toggle } = await open();
		expect(q('[data-options-panel]')).not.toBeNull();
		await toggle();
		const panel = q('[data-frame-panel]')!;
		expect(panel.querySelector('[data-settings-view="desktop"]')).not.toBeNull();
		expect(panel.querySelector('[data-options-panel]')).toBeNull();
		expect(q('[data-floating-prompt]')).not.toBeNull();
		expect(q('[data-prompt-settings]')?.getAttribute('aria-pressed')).toBe('true');
		expect(text('[data-settings-header] h2')).toBe('Settings');
		expect(text('[data-settings-header] [data-detail-back]')).toBe('Edit a region');
		expect(text('[data-settings-setup]')).toBe('Qwen-Image 2.1 · Edit · Soft portrait');
		view.destroy();
	});

	it('shows the form the server serves for the editor key, as it is', async () => {
		const { view, q, toggle } = await open();
		await toggle();
		const body = q('[data-settings-body]')!;
		expect(body.textContent).toContain('Strength');
		expect(body.textContent).toContain('Style notes');
		expect(api.getPresetFormSchema).toHaveBeenCalledWith('qwen', 'editor:edit');
		view.destroy();
	});

	it('hides nothing in the browser: a field the served form lists is shown', async () => {
		const { view, q, toggle } = await open(false, edit(), SESSIONS, SCHEMA_WITH_QUANTITY);
		await toggle();
		expect(q('[data-settings-body]')!.textContent).toContain('Quantity');
		view.destroy();
	});

	it('goes back through the header button, the prompt button and Esc, and Esc leaves the prompt open', async () => {
		const { view, q, toggle } = await open();
		await toggle();
		q<HTMLButtonElement>('[data-settings-header] [data-detail-back]')!.click();
		await settle(14);
		expect(q('[data-settings-view]')).toBeNull();
		expect(q('[data-options-panel]')).not.toBeNull();
		await toggle();
		await toggle();
		expect(q('[data-settings-view]')).toBeNull();
		await toggle();
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle(14);
		expect(q('[data-settings-view]')).toBeNull();
		expect(q('[data-floating-prompt]')).not.toBeNull();
		view.destroy();
	});

	it('folds the Edits list to its header row while open and restores it on close', async () => {
		const { view, q, toggle } = await open();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(false);
		expect(q('[data-stack-fold]')?.getAttribute('aria-expanded')).toBe('true');
		await toggle();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(true);
		expect(q('[data-stack-rows]')?.className).toContain('hidden');
		q<HTMLButtonElement>('[data-stack-fold]')!.click();
		await settle();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(false);
		await toggle();
		expect(q('[data-stack-fold]')?.getAttribute('aria-expanded')).toBe('true');
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(false);
		view.destroy();
	});
});

describe('Changed and saving through the session control', () => {
	async function edited(task?: EditorTaskInfo, sessions?: unknown[]) {
		const opened = await open(false, task, sessions);
		await opened.toggle();
		const notes = () => Array.from(opened.view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		return { ...opened, notes };
	}

	it('marks Changed in the header and next to the Settings button once a value differs, and unmarks it when it goes back', async () => {
		const { view, q, text, notes } = await edited();
		expect(q('[data-settings-changed]')).toBeNull();
		typeInto(notes(), 'oil paint');
		await settle(14);
		expect(text('[data-settings-changed]')).toBe('Changed');
		expect(text('[data-floating-prompt] [data-setup-changed]')).toBe('Changed');
		typeInto(notes(), '');
		await settle(14);
		expect(q('[data-settings-changed]')).toBeNull();
		expect(q('[data-setup-changed]')).toBeNull();
		view.destroy();
	});

	it('keeps no save buttons of its own: saving lives in the session control only', async () => {
		const { view, q, notes } = await edited();
		typeInto(notes(), 'oil paint');
		await settle(14);
		expect(q('[data-settings-save-to]')).toBeNull();
		expect(q('[data-settings-save-new]')).toBeNull();
		expect(q('[data-settings-footer]')).toBeNull();
		expect(view.target.querySelectorAll('[data-testid="session-save"]')).toHaveLength(1);
		view.destroy();
	});

	it('saves to the session from the control with the edited values, then clears Changed', async () => {
		const { view, q, ports, notes } = await edited();
		const update = ports.updateSession;
		typeInto(notes(), 'oil paint');
		await settle(14);
		q<HTMLButtonElement>('[data-testid="session-save"]')!.click();
		await settle(14);
		expect(update).toHaveBeenCalledTimes(1);
		const [id, body] = update.mock.calls[0] as unknown as [string, { base_version: number; kind: string; mode: string; data: Record<string, { formData: Record<string, unknown> }> }];
		expect(id).toBe('s1');
		expect(body.base_version).toBe(3);
		expect(body.kind).toBe('editor');
		expect(body.mode).toBe('editor:edit');
		expect(body.data['editor:edit'].formData).toMatchObject({ style_notes: 'oil paint', strength: 0.5 });
		expect(q('[data-settings-changed]')).toBeNull();
		view.destroy();
	});

	it('asks for a name when the model defaults are saved, words a clash with a Generate page session, then uses the new setup', async () => {
		const defaults = edit({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } });
		const { view, q, text, ports, notes } = await edited(defaults, []);
		expect(text('[data-settings-setup]')).toBe('Qwen-Image 2.1 · Edit · Model defaults');
		typeInto(notes(), 'oil paint');
		await settle(14);
		const save = ports.saveSession;
		save.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'session_kind_mismatch' } } } });
		q<HTMLButtonElement>('[data-testid="session-save"]')!.click();
		await settle(14);
		const name = document.body.querySelector<HTMLInputElement>('#session-save-name')!;
		expect(name).not.toBeNull();
		const confirm = () => Array.from(name.closest('[role="dialog"]')!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim().startsWith('Save'))!;
		typeInto(name, 'Held by Generate');
		await settle();
		confirm().click();
		await settle(14);
		expect(name.closest('[role="dialog"]')!.textContent).toContain('That name is already used by one of your sessions on the Generate page. Choose another name.');
		typeInto(name, 'Crisp');
		await settle();
		confirm().click();
		await settle(14);
		expect(save).toHaveBeenCalledTimes(2);
		expect((save.mock.calls[1] as unknown[])[0]).toMatchObject({ preset_id: 'qwen', name: 'Crisp', mode: 'editor:edit', kind: 'editor', data: { 'editor:edit': { formData: expect.objectContaining({ style_notes: 'oil paint' }) } } });
		expect(document.body.querySelector('#session-save-name')).toBeNull();
		expect(text('[data-settings-setup]')).toBe('Qwen-Image 2.1 · Edit · Crisp');
		view.destroy();
	});

	it('reloads the form from the last snapshot when the member discards the changes in the control', async () => {
		const { view, q, text, notes } = await edited();
		typeInto(notes(), 'oil paint');
		await settle(14);
		expect(text('[data-settings-changed]')).toBe('Changed');
		q<HTMLButtonElement>('[data-testid="session-reload"]')!.click();
		await settle(14);
		const confirm = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((button) => /^Reload\b/.test(button.textContent?.trim() ?? ''))!;
		confirm.click();
		await settle(30);
		expect(q('[data-settings-changed]')).toBeNull();
		expect(notes().value).toBe('');
		view.destroy();
	});
});

describe('switching setup with unsaved changes', () => {
	it('asks before another session replaces them, and keeps them on Keep editing', async () => {
		const { view, q, text, toggle, controller } = await open(false, edit(), [...SESSIONS, { id: 's2', name: 'Crisp', updated_at: null, version: 1, data: { 'editor:edit': { formData: { strength: 0.9 } } } }]);
		await toggle();
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		q<HTMLButtonElement>('[data-testid="session-trigger"]')!.click();
		await settle(14);
		document.body.querySelector<HTMLButtonElement>('[data-session-row="s2"]')!.click();
		await settle(14);
		expect(document.body.textContent).toContain('has changes that are not saved');
		expect(text('[data-testid="session-trigger"]')).toBe('Soft portrait');
		expect(controller.changedFor('edit_region')).toBe(true);
		view.destroy();
	});
});

describe('Settings on the phone', () => {
	it('replaces the prompt in the same dock with a back control, and no second sheet', async () => {
		const { view, q, toggle } = await open(true);
		expect(q('[data-floating-prompt="phone"]')).not.toBeNull();
		await toggle();
		expect(q('[data-settings-view="phone"]')).not.toBeNull();
		expect(q('[data-floating-prompt]')).toBeNull();
		expect(view.target.querySelectorAll('[data-frame-sheet]').length).toBe(0);
		q<HTMLButtonElement>('[data-settings-header] [data-detail-back]')!.click();
		await settle(14);
		expect(q('[data-settings-view]')).toBeNull();
		expect(q('[data-floating-prompt="phone"]')).not.toBeNull();
		view.destroy();
	});
});
