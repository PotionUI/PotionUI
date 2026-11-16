import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
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
const { activeConfirm, settleConfirm } = await import('$lib/stores/confirm');
const { ensureCoreImageEditor } = await import('$lib/tool-tabs/image/coreEditor');

registerBuiltinFieldComponents();
Element.prototype.scrollIntoView = () => {};

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({
	preset_id: 'qwen',
	preset_name: 'Qwen-Image 2.1',
	mode: 'edit',
	mode_label: 'Edit',
	label: null,
	variant: null,
	region_strategy: 'mask',
	via: null,
	qualifies: true,
	reason: null,
	owned: ['source_image', 'source_image_inpaint_mask', 'quantity'],
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
		offers: [offer(), offer({ mode: 'other', mode_label: 'Other' })],
		default: { preset_id: 'qwen', mode: 'edit', variant: null, setup: sessionSetup, health: { ok: true, reason: null, name: null, mode_label: null } },
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
						{ type: 'section', title: 'Image', children: [{ type: 'row', children: [{ name: 'quantity', type: 'integer', title: 'Quantity', default: 1 }] }] },
						{ name: 'style_notes', type: 'string', title: 'Style notes', default: '' }
					]
				},
				{ type: 'tab', name: 'references', label: 'References', children: [{ name: 'source_image', type: 'image', title: 'Reference picture' }] }
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

const SESSIONS = [{ id: 's1', name: 'Soft portrait', updated_at: null, version: 3, data: { edit: { formData: { strength: 0.5 } } } }];

async function open(phone = false, task: EditorTaskInfo = edit(), sessions: unknown[] = SESSIONS) {
	vi.spyOn(api, 'getPresetFormSchema').mockResolvedValue({ success: true, data: { preset_id: 'qwen', form_schema: SCHEMA } } as never);
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

	it('shows the mode form without the fields the editor fills itself', async () => {
		const { view, q, toggle } = await open();
		await toggle();
		const body = q('[data-settings-body]')!;
		expect(body.textContent).toContain('Strength');
		expect(body.textContent).toContain('Style notes');
		expect(body.textContent).not.toContain('Quantity');
		expect(body.textContent).not.toContain('Reference picture');
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
		expect(q('[data-stack-fold]')).toBeNull();
		await toggle();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(true);
		expect(q('[data-stack-rows]')?.className).toContain('hidden');
		q<HTMLButtonElement>('[data-stack-fold]')!.click();
		await settle();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(false);
		await toggle();
		expect(q('[data-stack-fold]')).toBeNull();
		expect(q('[data-edit-stack]')?.hasAttribute('data-folded')).toBe(false);
		view.destroy();
	});
});

describe('Changed and the pinned save buttons', () => {
	async function edited(task?: EditorTaskInfo, sessions?: unknown[]) {
		const opened = await open(false, task, sessions);
		await opened.toggle();
		const field = opened.view.target.querySelector<HTMLInputElement>('[data-settings-body] input[type="text"], [data-settings-body] input:not([type])');
		return { ...opened, field };
	}

	it('marks Changed in the header and on the chip once a value differs, and unmarks it when it goes back', async () => {
		const { view, q, text } = await edited();
		expect(q('[data-settings-changed]')).toBeNull();
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		expect(text('[data-settings-changed]')).toBe('Changed');
		expect(text('[data-setup-chip]')).toContain('Changed');
		typeInto(notes, '');
		await settle(14);
		expect(q('[data-settings-changed]')).toBeNull();
		expect(text('[data-setup-chip]')).not.toContain('Changed');
		view.destroy();
	});

	it('offers Save to the setup only for a changed setup of the member, next to Save as new setup', async () => {
		const { view, q, text } = await edited();
		expect(q('[data-settings-save-to]')).toBeNull();
		expect(q('[data-settings-save-new]')).not.toBeNull();
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		expect(text('[data-settings-save-to]')).toBe('Save to “Soft portrait”');
		view.destroy();
	});

	it('offers only Save as new setup for the model defaults', async () => {
		const defaults = edit({ default: { preset_id: 'qwen', mode: 'edit', variant: null, setup: { kind: 'defaults' }, health: { ok: true, reason: null, name: null, mode_label: null } } });
		const { view, q, text } = await edited(defaults, []);
		expect(text('[data-settings-setup]')).toBe('Qwen-Image 2.1 · Edit · Model defaults');
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		expect(q('[data-settings-changed]')).not.toBeNull();
		expect(q('[data-settings-save-to]')).toBeNull();
		expect(q('[data-settings-save-new]')).not.toBeNull();
		view.destroy();
	});

	it('saves to the setup, then clears Changed', async () => {
		const { view, q, ports } = await edited();
		const update = ports.updateSession;
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		q<HTMLButtonElement>('[data-settings-save-to]')!.click();
		await settle(14);
		expect(update).toHaveBeenCalledTimes(1);
		const [id, body] = update.mock.calls[0] as unknown as [string, { base_version: number; kind: string; data: Record<string, { formData: Record<string, unknown> }> }];
		expect(id).toBe('s1');
		expect(body.base_version).toBe(3);
		expect(body.kind).toBe('editor');
		expect(body.data.edit.formData).toMatchObject({ style_notes: 'oil paint', strength: 0.5 });
		expect(body.data.edit.formData).not.toHaveProperty('source_image');
		expect(body.data.edit.formData).not.toHaveProperty('quantity');
		expect(q('[data-settings-changed]')).toBeNull();
		expect(q('[data-settings-save-to]')).toBeNull();
		view.destroy();
	});

	it('asks for a name, saves a new setup and uses it, and words a clash with a Generate page session', async () => {
		const { view, q, text, ports } = await edited();
		const save = ports.saveSession;
		save.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'session_kind_mismatch' } } } });
		q<HTMLButtonElement>('[data-settings-save-new]')!.click();
		await settle(14);
		const dialog = document.body.querySelector('[data-save-setup-dialog]')!;
		expect(dialog).not.toBeNull();
		const name = dialog.querySelector<HTMLInputElement>('[data-save-setup-name]')!;
		typeInto(name, 'Held by Generate');
		await settle();
		const confirm = () => Array.from(dialog.closest('[role="dialog"]')!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim().startsWith('Save'))!;
		confirm().click();
		await settle(14);
		expect(document.body.querySelector('[data-save-setup-error]')?.textContent).toBe('That name is already used by one of your sessions on the Generate page. Choose another name.');
		typeInto(name, 'Crisp');
		await settle();
		confirm().click();
		await settle(14);
		expect(save).toHaveBeenCalledTimes(2);
		expect((save.mock.calls[1] as unknown[])[0]).toMatchObject({ preset_id: 'qwen', name: 'Crisp', mode: 'edit', kind: 'editor' });
		expect(document.body.querySelector('[data-save-setup-dialog]')).toBeNull();
		expect(text('[data-settings-setup]')).toBe('Qwen-Image 2.1 · Edit · Crisp');
		view.destroy();
	});
});

describe('switching setup with unsaved changes', () => {
	it('asks first, keeps the setup on Keep editing and switches on Discard', async () => {
		const { view, q, text, toggle } = await open();
		await toggle();
		const notes = Array.from(view.target.querySelectorAll<HTMLInputElement>('[data-settings-body] input')).find((input) => input.closest('label, div')?.textContent?.includes('Style notes'))!;
		typeInto(notes, 'oil paint');
		await settle(14);
		q<HTMLButtonElement>('[data-setup-chip]')!.click();
		await settle(14);
		const options = Array.from(view.target.querySelectorAll<HTMLButtonElement>('[data-picker-option]'));
		const defaults = options.find((option) => option.textContent?.includes('Model defaults'))!;
		defaults.click();
		await settle();
		q<HTMLButtonElement>('[data-picker-use]')!.click();
		await settle();
		const request = get(activeConfirm)!;
		expect(request.title).toBe('Discard your changes?');
		expect(request.message).toBe('Your changes to “Soft portrait” are used only for this edit unless you save them.');
		expect(request.cancelLabel).toBe('Keep editing');
		expect(request.confirmLabel).toBe('Discard');
		settleConfirm(request.id, false);
		await settle();
		expect(text('[data-setup-chip]')).toContain('Soft portrait');
		q<HTMLButtonElement>('[data-picker-use]')!.click();
		await settle();
		settleConfirm(get(activeConfirm)!.id, true);
		await settle(14);
		expect(text('[data-setup-chip]')).toContain('Model defaults');
		expect(text('[data-setup-chip]')).not.toContain('Changed');
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
