// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist (built by
// scripts/build-plugins.mjs) to prove the wizard wires itself to the global
// chat assistant host API (`window.__potionui.chat`): the
// "comfyui-import" mode + `comfyui_import` context provider are registered
// while the Design (Form) step is showing an analyzed workflow, unregistered
// on unmount, and an approved `propose_form_changes` tool result is applied
// to the live form via the same code paths a manual edit uses.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

const ANALYZE_RESULT = {
	mode: 'text2img',
	node_count: 4,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	candidates: [
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'seed', current_value: 42, value_type: 'int', suggested_field_type: 'seed', suggested_field_name: 'seed', suggested_label: 'Seed', suggested_config: {}, role: 'seed' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'sampler_name', current_value: 'euler', value_type: 'str', suggested_field_type: 'select', suggested_field_name: 'sampler_name', suggested_label: 'Sampler', suggested_config: {}, role: 'sampler' },
		{ node_id: '9', class_type: 'LatentUpscale', node_title: 'LatentUpscale', input_name: 'width', current_value: 1024, value_type: 'int', suggested_field_type: 'number', suggested_field_name: 'upscale_width', suggested_label: 'Upscale width', suggested_config: {}, role: 'other' }
	],
	default_form: { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] },
	default_history: []
};

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-chat-${Math.random().toString(36).slice(2)}.mjs`);
	copyFileSync(resolve(REPO_ROOT, DIST_PATH), staged);
	const mod = await import(/* @vite-ignore */ pathToFileURL(staged).href);
	return mod.default;
}

function target(): HTMLDivElement {
	const el = document.createElement('div');
	document.body.appendChild(el);
	return el;
}

async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	await new Promise((r) => setTimeout(r, 0));
}

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400) {
	return { ok, status, json: async () => body } as Response;
}

function stubChatHost() {
	const providers = new Map<string, () => unknown>();
	const toolHandlers = new Map<string, ((result: unknown) => void)[]>();
	const unregisterCounts = { context: 0, mode: 0, tool: 0 };

	const chat = {
		provideContext: vi.fn((key: string, provider: () => unknown) => {
			providers.set(key, provider);
			return vi.fn(() => {
				providers.delete(key);
				unregisterCounts.context += 1;
			});
		}),
		declareMode: vi.fn((_modeId: string) => {
			return vi.fn(() => {
				unregisterCounts.mode += 1;
			});
		}),
		onToolApplied: vi.fn((toolName: string, handler: (result: unknown) => void) => {
			const list = toolHandlers.get(toolName) || [];
			list.push(handler);
			toolHandlers.set(toolName, list);
			return vi.fn(() => {
				unregisterCounts.tool += 1;
			});
		})
	};

	const notifications = { toast: vi.fn(), notify: vi.fn(async () => {}) };

	(window as unknown as { __potionui: unknown }).__potionui = { chat, notifications };

	return { chat, notifications, providers, toolHandlers, unregisterCounts };
}

let ImportWorkflowTab: any;

beforeAll(async () => {
	if (!existsSync(resolve(REPO_ROOT, DIST_PATH))) {
		throw new Error(`${DIST_PATH} is missing - run \`node scripts/build-plugins.mjs comfyui-backend\` first.`);
	}
	const raw = await loadDist();
	ImportWorkflowTab = _wrapPluginDistComponent(raw);
});

afterEach(() => {
	vi.unstubAllGlobals();
	delete (window as unknown as { __potionui?: unknown }).__potionui;
	document.body.innerHTML = '';
});

async function mountOnFormStep(el: HTMLDivElement, analyzeResult: unknown = ANALYZE_RESULT) {
	const fetchMock = vi.fn(async (url: string) => {
		if (url === '/api/fields/types') return jsonResponse({ success: true, data: [{ type: 'select', container: false }, { type: 'number', container: false }] });
		if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
		if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(analyzeResult);
		throw new Error(`Unexpected fetch: ${url}`);
	});
	vi.stubGlobal('fetch', fetchMock);

	const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
	await settle();

	const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
	textarea.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} } });
	textarea.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
	el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
	await settle();

	return instance;
}

describe('ImportWorkflowTab chat assistant bridge (real compiled dist)', () => {
	it('registers the comfyui-import mode + context provider once the Design step shows an analyzed workflow, and unregisters on unmount', async () => {
		const el = target();
		const host = stubChatHost();

		const instance = await mountOnFormStep(el);

		expect(host.chat.declareMode).toHaveBeenCalledWith('comfyui-import');
		expect(host.chat.provideContext).toHaveBeenCalledWith('comfyui_import', expect.any(Function));
		expect(host.chat.onToolApplied).toHaveBeenCalledWith('propose_form_changes', expect.any(Function));

		unmount(instance);
		await settle();

		expect(host.unregisterCounts.context).toBe(1);
		expect(host.unregisterCounts.mode).toBe(1);
		expect(host.unregisterCounts.tool).toBe(1);
	});

	it('does not register anything while still on the Source step (no analysis yet)', async () => {
		const el = target();
		const host = stubChatHost();

		const fetchMock = vi.fn(async () => jsonResponse({ families: [] }));
		vi.stubGlobal('fetch', fetchMock);
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		expect(host.chat.declareMode).not.toHaveBeenCalled();
		expect(host.chat.provideContext).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('the registered context provider returns the wire contract shape', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;

		expect(ctx.format).toBe('api');
		expect(ctx.node_count).toBe(4);
		expect(typeof ctx.workflow_name).toBe('string');

		const seedCandidate = ctx.candidates.find((c: any) => c.input_name === 'seed');
		expect(seedCandidate.locked).toBe(true);
		const samplerCandidate = ctx.candidates.find((c: any) => c.input_name === 'sampler_name');
		expect(samplerCandidate.locked).toBe(false);
		// Wire-contract fields only - no leaking of local-only wizard fields.
		expect(Object.keys(seedCandidate).sort()).toEqual(
			['class_type', 'current_value', 'input_name', 'locked', 'node_id', 'node_title', 'role', 'suggested_field_type', 'value_type'].sort()
		);

		expect(ctx.form.tabs).toEqual([{ id: 'generation', label: 'Generation', items: [] }]);
		expect(ctx.mapped).toEqual([]);

		unmount(instance);
	});

	it('applies add_tab + add_field + map ops from an approved propose_form_changes result', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const handler = host.toolHandlers.get('propose_form_changes')![0];
		handler({
			action: 'apply_import_form_changes',
			ops: [
				{ op: 'add_tab', label: 'Assistant', id: 'assistant' },
				{
					op: 'add_field',
					tab: 'assistant',
					field_type: 'number',
					field_name: 'upscale_width',
					label: 'Upscale width',
					mappings: [{ node_id: '9', input_name: 'width', transform: 'none' }]
				},
				{ op: 'map', field_name: 'upscale_width', node_id: '3', input_name: 'sampler_name', transform: 'none' }
			]
		});
		await settle();

		const newTab = el.querySelector('[data-tab-id="assistant"]');
		expect(newTab).toBeTruthy();
		expect(newTab?.textContent).toContain('Assistant');
		expect(newTab?.getAttribute('aria-selected')).toBe('true');

		const card = el.querySelector('[data-field-name="upscale_width"]');
		expect(card).toBeTruthy();
		const mappingText = card?.querySelector('.di-mapping')?.textContent || '';
		expect(mappingText).toContain('9.inputs.width');
		expect(mappingText).toContain('3.inputs.sampler_name');

		expect(host.notifications.toast).toHaveBeenCalledWith('success', expect.stringContaining('Applied 3 changes'));

		unmount(instance);
	});

	it('skips a map op onto a locked candidate without throwing, and applies the rest of the batch', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

		const handler = host.toolHandlers.get('propose_form_changes')![0];
		expect(() =>
			handler({
				action: 'apply_import_form_changes',
				ops: [
					{
						op: 'add_field',
						tab: 'generation',
						field_type: 'number',
						field_name: 'weird_seed_field',
						label: 'Weird seed field',
						mappings: [{ node_id: '3', input_name: 'seed', transform: 'none' }]
					}
				]
			})
		).not.toThrow();
		await settle();

		expect(warnSpy).toHaveBeenCalled();
		const card = el.querySelector('[data-field-name="weird_seed_field"]');
		expect(card).toBeTruthy();
		expect(card?.querySelector('.di-mapping')).toBeNull();

		warnSpy.mockRestore();
		unmount(instance);
	});
});
