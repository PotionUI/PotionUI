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

	it('applies add_tab + add_field + map ops from an approved propose_form_changes result tagged with the current draft', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];
		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctx.draft_id,
			form_revision: ctx.form_revision,
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

		expect(outcome).toEqual({ status: 'applied', applied: 3, skipped: 0 });

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

	it('an add_field op whose only mapping targets a locked candidate is skipped entirely (the field is not created), without throwing', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];
		let outcome: any;
		expect(() => {
			outcome = handler({
				action: 'apply_import_form_changes',
				draft_id: ctx.draft_id,
				form_revision: ctx.form_revision,
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
			});
		}).not.toThrow();
		await settle();

		expect(warnSpy).toHaveBeenCalled();
		// All-or-nothing: a mapping the op can't have (seed is locked) means
		// the whole op - including the field itself - is skipped, not a
		// field created with a hole where that mapping would be.
		expect(outcome).toEqual({
			status: 'stale',
			applied: 0,
			skipped: 1,
			message: expect.stringContaining('could be applied')
		});
		expect(el.querySelector('[data-field-name="weird_seed_field"]')).toBeNull();
		expect(host.notifications.toast).not.toHaveBeenCalled();

		warnSpy.mockRestore();
		unmount(instance);
	});

	it('applies a propose_form_changes result whose draft_id/form_revision match the wizard\'s current context (control)', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctx.draft_id,
			form_revision: ctx.form_revision,
			ops: [{ op: 'add_tab', label: 'Assistant', id: 'assistant' }]
		});
		await settle();

		expect(outcome).toEqual({ status: 'applied', applied: 1, skipped: 0 });
		expect(el.querySelector('[data-tab-id="assistant"]')).toBeTruthy();
		expect(host.notifications.toast).toHaveBeenCalledWith('success', expect.stringContaining('Applied 1 change'));

		unmount(instance);
	});

	it('reports "stale" for a result carrying no draft_id at all - it has no provable owner', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const handler = host.toolHandlers.get('propose_form_changes')![0];
		const outcome = handler({
			action: 'apply_import_form_changes',
			ops: [{ op: 'add_tab', label: 'Assistant', id: 'assistant' }]
		});
		await settle();

		expect(outcome).toEqual({
			status: 'stale',
			applied: 0,
			skipped: 1,
			message: 'This proposal predates the current import session, so nothing was applied. Ask for a fresh proposal.'
		});
		expect(el.querySelector('[data-tab-id="assistant"]')).toBeNull();
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('a proposal from a torn-down mount is reported stale even against a fresh mount that lands on the same local sourceToken count (draft_id must not collide across instances)', async () => {
		const el1 = target();
		const host1 = stubChatHost();
		const instance1 = await mountOnFormStep(el1);

		const draftIdMount1 = (host1.providers.get('comfyui_import')!() as any).draft_id;

		unmount(instance1);
		await settle();
		document.body.innerHTML = '';

		// A second, independent mount that goes through the EXACT same
		// sequence (mountOnFormStep: one textarea edit, one analyze) as the
		// first - its local sourceToken/formRevision counters land on the
		// same values the first mount had, which is exactly the collision a
		// counter-derived draft_id would have had.
		const el2 = target();
		const host2 = stubChatHost();
		const instance2 = await mountOnFormStep(el2);

		const draftIdMount2 = (host2.providers.get('comfyui_import')!() as any).draft_id;
		expect(draftIdMount2).not.toBe(draftIdMount1);

		const handlerMount2 = host2.toolHandlers.get('propose_form_changes')![0];
		const outcome = handlerMount2({
			action: 'apply_import_form_changes',
			draft_id: draftIdMount1,
			form_revision: 0,
			ops: [{ op: 'add_tab', label: 'Assistant', id: 'assistant' }]
		});
		await settle();

		expect(outcome).toMatchObject({ status: 'stale', applied: 0 });
		expect(el2.querySelector('[data-tab-id="assistant"]')).toBeNull();
		expect(host2.notifications.toast).not.toHaveBeenCalled();

		unmount(instance2);
	});

	it('reports "stale" (and never mutates the form) for a proposal built for a workflow that was replaced with "Change workflow"', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const provider = host.providers.get('comfyui_import')!;
		const draftIdA = (provider() as any).draft_id;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		const changeWorkflowBtn = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Change workflow'))!;
		changeWorkflowBtn.click();
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} }, marker: 'B' });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();
		expect(el.querySelector('[data-wiz-step="form"]')?.className).toContain('current');

		const draftIdB = (provider() as any).draft_id;
		expect(draftIdB).not.toBe(draftIdA);

		// A proposal the assistant built while workflow A was loaded, approved
		// only now that B is on screen.
		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: draftIdA,
			form_revision: 0,
			ops: [{ op: 'add_tab', label: 'Assistant', id: 'assistant' }]
		});
		await settle();

		expect(outcome).toMatchObject({ status: 'stale', applied: 0 });
		expect(el.querySelector('[data-tab-id="assistant"]')).toBeNull();
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('reports "stale" for a proposal approved after "Import another" reset the wizard', async () => {
		const el = target();
		const host = stubChatHost();

		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(ANALYZE_RESULT);
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') return jsonResponse({ results: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				return jsonResponse({ preset_id: 'PRESET1', path: 'content/presets/local/SDXL/imported', mode: 'text2img', lint: { errors: [], warnings: [] } });
			}
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

		const provider = host.providers.get('comfyui_import')!;
		const draftIdBeforeReset = (provider() as any).draft_id;
		// Captured while still registered - the stub's unregister doesn't drop
		// it from this list, matching the real host: calling a handler after
		// its owner un-registers is not a path either side needs to guard
		// (dispatchToolApplied would simply find nothing registered), so this
		// exercises the wizard's OWN post-reset state directly.
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
		familyInput.value = 'SDXL';
		familyInput.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
		nameInput.value = 'Import A';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();
		expect(el.querySelector('[data-wiz-step="done"]')?.className).toContain('current');

		const importAnotherBtn = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Import another'))!;
		importAnotherBtn.click();
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: draftIdBeforeReset,
			form_revision: 0,
			ops: [{ op: 'add_tab', label: 'Assistant', id: 'assistant' }]
		});
		await settle();

		expect(outcome).toMatchObject({ status: 'stale', applied: 0 });
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('revalidates against an intervening manual edit on the SAME draft: a still-compatible op applies, an op the edit invalidated is skipped, and the manual edit is preserved', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		// Manually map the sampler_name candidate to a field before the
		// "proposal" this test simulates was supposedly built.
		el.querySelector<HTMLButtonElement>('[data-input-key="3:sampler_name"] [data-action="add-input"]')!.click();
		await settle();
		expect(el.querySelector('[data-field-name="sampler_name"]')).toBeTruthy();

		const provider = host.providers.get('comfyui_import')!;
		const ctxAtProposal = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		// Intervening manual edit: the user removes that same field - a newer
		// change the proposal (built before it) knows nothing about.
		el.querySelector<HTMLButtonElement>('[data-field-name="sampler_name"] [data-action="remove"]')!.click();
		await settle();
		expect(el.querySelector('[data-field-name="sampler_name"]')).toBeNull();

		const ctxNow = provider() as any;
		expect(ctxNow.draft_id).toBe(ctxAtProposal.draft_id);
		expect(ctxNow.form_revision).toBeGreaterThan(ctxAtProposal.form_revision);

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctxAtProposal.draft_id,
			form_revision: ctxAtProposal.form_revision,
			ops: [
				{ op: 'add_tab', label: 'Assistant', id: 'assistant' },
				{ op: 'map', field_name: 'sampler_name', node_id: '9', input_name: 'width', transform: 'none' }
			]
		});
		await settle();

		expect(outcome).toEqual({
			status: 'partial',
			applied: 1,
			skipped: 1,
			message: expect.stringContaining('1 change')
		});
		expect(el.querySelector('[data-tab-id="assistant"]')).toBeTruthy();
		// The user's deletion is not undone by the stale op that referenced it.
		expect(el.querySelector('[data-field-name="sampler_name"]')).toBeNull();
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('an op naming a tab that has since been removed is skipped, never retargeted onto the active/first tab', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-action="add-tab"]')!.click();
		await settle();
		const secondTab = el.querySelector('[data-tab-id="tab_2"]')!;
		expect(secondTab).toBeTruthy();

		secondTab.querySelector<HTMLButtonElement>('[data-action="tab-menu"]')!.click();
		await settle();
		document.querySelector<HTMLButtonElement>('[data-action="delete-tab"]:not([disabled])')!.click();
		await settle();
		expect(el.querySelector('[data-tab-id="tab_2"]')).toBeNull();

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctx.draft_id,
			form_revision: ctx.form_revision,
			ops: [
				{
					op: 'add_field',
					tab: 'tab_2',
					field_type: 'number',
					field_name: 'upscale_width',
					label: 'Upscale width',
					mappings: [{ node_id: '9', input_name: 'width', transform: 'none' }]
				}
			]
		});
		await settle();

		expect(outcome).toEqual({
			status: 'stale',
			applied: 0,
			skipped: 1,
			message: expect.stringContaining('could be applied')
		});
		// Not retargeted onto "generation" (the active/first tab) either.
		expect(el.querySelector('[data-field-name="upscale_width"]')).toBeNull();
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('a mapping the user claimed for their own field after the proposal was built is not stolen by a stale op targeting it', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const provider = host.providers.get('comfyui_import')!;
		const ctxAtProposal = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		// The user maps node 9's "width" candidate to their own field AFTER
		// the (simulated) proposal above was built.
		el.querySelector<HTMLButtonElement>('[data-input-key="9:width"] [data-action="add-input"]')!.click();
		await settle();
		const usersCard = el.querySelector('[data-field-name="upscale_width"]')!;
		expect(usersCard).toBeTruthy();
		expect(usersCard.querySelector('.di-mapping')?.textContent).toContain('9.inputs.width');

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctxAtProposal.draft_id,
			form_revision: ctxAtProposal.form_revision,
			ops: [
				{ op: 'add_tab', label: 'Assistant', id: 'assistant' },
				{
					op: 'add_field',
					tab: 'generation',
					field_type: 'number',
					field_name: 'stale_width',
					label: 'Stale width',
					mappings: [{ node_id: '9', input_name: 'width', transform: 'none' }]
				}
			]
		});
		await settle();

		expect(outcome).toEqual({
			status: 'partial',
			applied: 1,
			skipped: 1,
			message: expect.stringContaining('1 change')
		});
		expect(el.querySelector('[data-tab-id="assistant"]')).toBeTruthy();
		// The stale op's field is never created (all-or-nothing: its only
		// mapping is unavailable), and the user's own field/mapping survives
		// untouched - checked via context (the batch's add_tab switched the
		// active tab away from "generation", where upscale_width lives, so
		// its card isn't the one currently rendered in the DOM).
		expect(el.querySelector('[data-field-name="stale_width"]')).toBeNull();
		const ctxAfter = provider() as any;
		expect(ctxAfter.mapped).toContainEqual({ field_name: 'upscale_width', node_id: '9', input_name: 'width', transform: 'none' });
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	// Sets up a field mapped to node 3's "sampler_name" candidate (default
	// transform 'none') and returns the mapping editor's transform <select>
	// for it, expanding the card's mapping editor first.
	async function mapSamplerNameAndGetTransformSelect(el: HTMLElement) {
		el.querySelector<HTMLButtonElement>('[data-input-key="3:sampler_name"] [data-action="add-input"]')!.click();
		await settle();
		const card = el.querySelector('[data-field-name="sampler_name"]')!;
		card.querySelector<HTMLButtonElement>('[data-action="toggle-mapping"]')!.click();
		await settle();
		const editor = card.querySelector('[data-mapping-editor]')!;
		const row = editor.querySelector('[data-mapedit-key="3:sampler_name"]')!;
		const select = row.querySelector<HTMLSelectElement>('.di-mapedit-transform select')!;
		return { card, select };
	}

	it('a stale map op requesting a different transform than the one the user set since is skipped - the user\'s transform is preserved', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const { card, select } = await mapSamplerNameAndGetTransformSelect(el);
		expect(select.value).toBe('none');

		const provider = host.providers.get('comfyui_import')!;
		const ctxAtProposal = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		// Intervening manual edit: the user picks a transform themselves.
		select.value = 'strip_model_prefix';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		const ctxNow = provider() as any;
		expect(ctxNow.draft_id).toBe(ctxAtProposal.draft_id);
		expect(ctxNow.form_revision).toBeGreaterThan(ctxAtProposal.form_revision);

		// The (stale) proposal wants "none" - what the mapping had when it was
		// built - but the user has since set it to something else.
		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctxAtProposal.draft_id,
			form_revision: ctxAtProposal.form_revision,
			ops: [{ op: 'map', field_name: 'sampler_name', node_id: '3', input_name: 'sampler_name', transform: 'none' }]
		});
		await settle();

		expect(outcome).toEqual({
			status: 'stale',
			applied: 0,
			skipped: 1,
			message: expect.stringContaining('form changed')
		});
		expect(card.querySelector('.di-mapping')?.textContent).toContain('3.inputs.sampler_name');
		expect(select.value).toBe('strip_model_prefix');
		expect(host.notifications.toast).not.toHaveBeenCalled();

		unmount(instance);
	});

	it('a current-revision map op updating a mapping\'s transform is applied', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const { card, select } = await mapSamplerNameAndGetTransformSelect(el);
		expect(select.value).toBe('none');

		const provider = host.providers.get('comfyui_import')!;
		const ctx = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctx.draft_id,
			form_revision: ctx.form_revision,
			ops: [{ op: 'map', field_name: 'sampler_name', node_id: '3', input_name: 'sampler_name', transform: 'strip_model_prefix' }]
		});
		await settle();

		expect(outcome).toEqual({ status: 'applied', applied: 1, skipped: 0 });
		expect(select.value).toBe('strip_model_prefix');
		expect(host.notifications.toast).toHaveBeenCalledWith('success', expect.stringContaining('Applied 1 change'));

		unmount(instance);
	});

	it('an older-revision map op requesting the SAME transform the mapping already has is a no-op that still counts as applied', async () => {
		const el = target();
		const host = stubChatHost();
		const instance = await mountOnFormStep(el);

		const { card, select } = await mapSamplerNameAndGetTransformSelect(el);
		expect(select.value).toBe('none');

		const provider = host.providers.get('comfyui_import')!;
		const ctxAtProposal = provider() as any;
		const handler = host.toolHandlers.get('propose_form_changes')![0];

		// An unrelated manual edit bumps the revision past the proposal's -
		// the transform itself is left exactly as it was.
		el.querySelector<HTMLButtonElement>('[data-action="add-tab"]')!.click();
		await settle();
		const ctxNow = provider() as any;
		expect(ctxNow.form_revision).toBeGreaterThan(ctxAtProposal.form_revision);

		const outcome = handler({
			action: 'apply_import_form_changes',
			draft_id: ctxAtProposal.draft_id,
			form_revision: ctxAtProposal.form_revision,
			ops: [{ op: 'map', field_name: 'sampler_name', node_id: '3', input_name: 'sampler_name', transform: 'none' }]
		});
		await settle();

		expect(outcome).toEqual({ status: 'applied', applied: 1, skipped: 0 });
		expect(select.value).toBe('none');
		expect(card.querySelector('.di-mapping')?.textContent).toContain('3.inputs.sampler_name');

		unmount(instance);
	});
});
