// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const template = {
	session: { id: '01JC0SESSION', name: 'Portrait', preset_id: 'p', preset_name: 'P', modes: ['txt2img', 'img2img'], mode: 'txt2img', settings_hash: 'abc' },
	prompt: { positive: 'a lighthouse', negative: '', batch: 1 },
	shortcuts: { seed: 'seed', count: 'quantity' },
	fields: [
		{ name: 'steps', label: 'Steps', type: 'slider', kind: 'value', value: 30, locked: false, media: false },
		{ name: 'cfg', label: 'Cfg', type: 'slider', kind: 'value', locked: true, media: false }
	],
	problems: [{ code: 'input_missing', message: 'The input file is gone.' }],
	editor_only: null
};

const api = {
	getSessionScriptTemplate: vi.fn(),
	listApiKeys: vi.fn()
};

vi.mock('$lib/services/api/index', () => ({ api }));

const { default: SessionScriptDialog } = await import('../../src/lib/components/session/SessionScriptDialog.svelte');

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: object | undefined;

function open(onClose = vi.fn()) {
	instance = mount(SessionScriptDialog as never, {
		target: document.body,
		props: { sessionId: '01JC0SESSION', sessionName: 'Portrait', onClose }
	});
}

beforeEach(() => {
	api.getSessionScriptTemplate.mockResolvedValue({ success: true, data: template });
	api.listApiKeys.mockResolvedValue({ success: true, data: { keys: [], limits: {} } });
});

afterEach(() => {
	if (instance) unmount(instance as never);
	instance = undefined;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('SessionScriptDialog', () => {
	it('shows the session id, the changeable fields, locked marks and problems', async () => {
		open();
		await settle();
		const text = document.body.textContent ?? '';

		expect(text).toContain('01JC0SESSION');
		expect(text).toContain('form.steps');
		expect(text).toContain('locked by an administrator');
		expect(text).toContain('The input file is gone.');
	});

	it('renders the curl example with the key as a placeholder, never a secret', async () => {
		open();
		await settle();
		const code = document.body.querySelector('pre')?.textContent ?? '';

		expect(code).toContain('/api/sessions/01JC0SESSION/generate');
		expect(code).toContain('$POTIONUI_KEY');
		expect(code).not.toMatch(/pui_[A-Za-z0-9]/);
	});

	it('offers to create a key when none can generate, and not when one can', async () => {
		open();
		await settle();
		expect(document.body.textContent).toContain('Create a key');
		unmount(instance as never);
		document.body.innerHTML = '';
		api.listApiKeys.mockResolvedValue({ success: true, data: { keys: [{ status: 'active', scopes: ['read', 'generate'] }], limits: {} } });
		open();
		await settle();
		expect(document.body.textContent).not.toContain('Create a key');
	});

	it('names the mode when the session holds several and reloads for another', async () => {
		open();
		await settle();
		const button = [...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'img2img');
		button?.click();
		await settle();

		expect(api.getSessionScriptTemplate).toHaveBeenLastCalledWith('01JC0SESSION', 'img2img');
	});

	it('says the editor is needed instead of showing examples for a Director session', async () => {
		api.getSessionScriptTemplate.mockResolvedValue({ success: true, data: { ...template, editor_only: { supported: 'x', message: 'Start it from the Generate page.' } } });
		open();
		await settle();

		expect(document.body.textContent).toContain('Start it from the Generate page.');
		expect(document.body.querySelector('pre')).toBeNull();
	});
});
