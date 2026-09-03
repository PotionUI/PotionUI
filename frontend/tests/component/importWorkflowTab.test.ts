// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist (built by
// scripts/build-plugins.mjs) through the host runtime the way the core
// admin plugin-detail tab strip does (resolvePluginComponent ->
// _wrapPluginDistComponent), mirroring pluginDistHostMount.test.ts's
// stage/load approach. Exercises the 5-step wizard end to end: paste a
// workflow -> Continue (analyze, mocked) -> Form step (default_form
// pre-populated, add an unmapped input, merge a resolution pair) ->
// Continue -> History step (toggle a row on) -> Continue -> Requirements
// (mocked) -> Continue (create, mocked) -> Done step lint result.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

const ANALYZE_RESULT = {
	mode: 'text2img',
	node_count: 7,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	candidates: [
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'seed', current_value: 619589674328597, value_type: 'int', suggested_field_type: 'seed', suggested_field_name: 'seed', suggested_label: 'Seed', suggested_config: {}, role: 'seed' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'steps', current_value: 27, value_type: 'int', suggested_field_type: 'slider', suggested_field_name: 'steps', suggested_label: 'Steps', suggested_config: { min: 1, max: 150, step: 1 }, role: 'steps' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'cfg', current_value: 3.5, value_type: 'float', suggested_field_type: 'number', suggested_field_name: 'cfg', suggested_label: 'CFG Scale', suggested_config: {}, role: 'cfg' },
		{ node_id: '5', class_type: 'EmptyLatentImage', node_title: 'Empty Latent Image', input_name: 'width', current_value: 832, value_type: 'int', suggested_field_type: 'resolution', suggested_field_name: 'resolution', suggested_label: 'Resolution', suggested_config: {}, role: 'resolution_width' },
		{ node_id: '5', class_type: 'EmptyLatentImage', node_title: 'Empty Latent Image', input_name: 'height', current_value: 1216, value_type: 'int', suggested_field_type: 'resolution', suggested_field_name: 'resolution', suggested_label: 'Resolution', suggested_config: {}, role: 'resolution_height' }
	],
	default_form: {
		tabs: [
			{
				id: 'generation',
				label: 'Generation',
				icon: null,
				items: [{ kind: 'field', field_name: 'steps', field_type: 'slider', label: 'Steps', default: 27, config: { min: 1, max: 150, step: 1 }, mappings: [{ node_id: '3', input_name: 'steps', transform: 'none' }] }]
			}
		]
	},
	default_history: [{ field: 'steps', label: 'Steps', format: 'number', template: null }]
};

const REQUIREMENTS_RESULT = {
	results: [
		{ type: 'comfyui_node', name: 'FaceDetailer', status: 'missing', detail: 'not installed', hint: 'install the pack' },
		{ type: 'comfyui_model', name: 'sdxlBase_v10.safetensors', status: 'ok', detail: 'present', hint: null }
	]
};

const IMPORT_RESULT = {
	preset_id: 'PRESET123',
	path: 'content/presets/local/SDXL/imported',
	mode: 'text2img',
	lint: { errors: [], warnings: [] }
};

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-${Math.random().toString(36).slice(2)}.mjs`);
	copyFileSync(resolve(REPO_ROOT, DIST_PATH), staged);
	const mod = await import(/* @vite-ignore */ pathToFileURL(staged).href);
	return mod.default;
}

function target(): HTMLDivElement {
	const el = document.createElement('div');
	document.body.appendChild(el);
	return el;
}

// The dist bundles its OWN Svelte runtime, whose scheduler flushes on a
// macrotask under jsdom (no native requestAnimationFrame) - see
// pluginDistHostMount.test.ts. Every interaction with the mounted dist needs
// this before asserting on the DOM it renders.
async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	await new Promise((r) => setTimeout(r, 0));
}

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400) {
	return { ok, status, json: async () => body } as Response;
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
	document.body.innerHTML = '';
});

describe('ImportWorkflowTab (real compiled dist)', () => {
	it('walks the full wizard: analyze -> form -> history -> requirements -> create -> done', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') {
				return jsonResponse({ success: true, data: [{ type: 'slider', container: false }, { type: 'number', container: false }, { type: 'resolution', container: false }] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: ['SDXL', 'Flux1'] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(ANALYZE_RESULT);
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') return jsonResponse(REQUIREMENTS_RESULT);
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				const body = JSON.parse(String(init?.body));
				expect(body.model_family).toBe('SDXL');
				expect(body.display_name).toBe('My import');
				const fieldNames = body.form.tabs[0].items.map((it: any) => it.field_name).sort();
				expect(fieldNames).toEqual(['cfg', 'resolution', 'steps']);
				const resolutionField = body.form.tabs[0].items.find((it: any) => it.field_name === 'resolution');
				expect(resolutionField.mappings.map((m: any) => m.transform).sort()).toEqual(['split_wh_height', 'split_wh_width']);
				expect(body.history).toEqual([{ field: 'steps', label: 'Steps', format: 'number', template: null }]);
				return jsonResponse(IMPORT_RESULT);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend', plugin: { id: 'comfyui-backend' } } });
		await settle();

		expect(el.querySelector('[data-import-wizard]')).toBeTruthy();

		// Step 1: Source.
		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} } });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		// Step 2: Form - default_form pre-populates the "steps" field card.
		expect(el.querySelector('[data-wiz-step="form"]')?.className).toContain('current');
		expect(el.querySelector('[data-field-name="steps"]')).toBeTruthy();

		// Left pane: seed is locked, cfg is an unmapped Add candidate.
		const seedRow = el.querySelector('[data-input-key="3:seed"]')!;
		expect(seedRow.textContent).toContain('always wired');
		expect(seedRow.querySelector('[data-action="add-input"]')).toBeNull();

		const cfgRow = el.querySelector('[data-input-key="3:cfg"]')!;
		cfgRow.querySelector<HTMLButtonElement>('[data-action="add-input"]')!.click();
		await settle();
		expect(el.querySelector('[data-field-name="cfg"]')).toBeTruthy();
		expect(el.querySelector('[data-input-key="3:cfg"]')?.className).toContain('mapped');

		// Resolution: two candidates sharing suggested_field_name merge into one field.
		el.querySelector<HTMLButtonElement>('[data-input-key="5:width"] [data-action="add-input"]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('[data-input-key="5:height"] [data-action="add-input"]')!.click();
		await settle();
		const resolutionCard = el.querySelector('[data-field-name="resolution"]')!;
		expect(resolutionCard).toBeTruthy();
		expect(el.querySelectorAll('[data-field-name="resolution"]')).toHaveLength(1);
		expect(resolutionCard.querySelector('.di-mapping')?.textContent).toContain('5.inputs.width');
		expect(resolutionCard.querySelector('.di-mapping')?.textContent).toContain('5.inputs.height');

		const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
		familyInput.value = 'SDXL';
		familyInput.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
		nameInput.value = 'My import';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
		await settle();

		// Step 3: History - "steps" arrives pre-enabled from default_history; the
		// two fields added on the Form step start disabled.
		expect(el.querySelector('[data-wiz-step="history"]')?.className).toContain('current');
		const stepsRow = el.querySelector('[data-history-row="steps"]')!;
		expect(stepsRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
		const cfgHistRow = el.querySelector('[data-history-row="cfg"]')!;
		expect(cfgHistRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(false);
		expect(el.querySelector('[data-history-preview]')?.textContent).toContain('Steps');
		expect(el.querySelector('[data-history-preview]')?.textContent).not.toContain('CFG Scale');

		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();

		// Step 4: Requirements (fetched automatically on entering the step).
		expect(el.querySelector('[data-wiz-step="requirements"]')?.className).toContain('current');
		const reqRows = el.querySelectorAll('[data-import-requirements] .req-row');
		expect(reqRows).toHaveLength(2);

		const createBtn = el.querySelector<HTMLButtonElement>('button[data-import-create]')!;
		expect(createBtn.disabled).toBe(false);
		createBtn.click();
		await settle();

		// Step 5: Done.
		expect(el.querySelector('[data-wiz-step="done"]')?.className).toContain('current');
		const lint = el.querySelector('[data-import-lint]')!;
		expect(lint.textContent).toContain('Lint clean');

		unmount(instance);
	});

	it('shows the backend message verbatim when a UI-format workflow needs a reachable ComfyUI backend', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				return jsonResponse({ detail: 'A reachable ComfyUI backend is needed to import UI-format workflows; use Export (API) or configure the backend' }, false, 400);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ nodes: [], links: [] });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		const error = el.querySelector('[data-import-analyze-error]');
		expect(error?.textContent).toBe('A reachable ComfyUI backend is needed to import UI-format workflows; use Export (API) or configure the backend');
		expect(el.querySelector('[data-import-form-inputs]')).toBeNull();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		unmount(instance);
	});

	it('lets "Change workflow" reset back to step 1 from the Form step', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(ANALYZE_RESULT);
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} } });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		expect(el.querySelector('[data-import-form-inputs]')).toBeTruthy();

		const changeLink = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Change workflow'));
		expect(changeLink).toBeTruthy();
		changeLink!.click();
		await settle();

		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');
		expect(el.querySelector('textarea[data-import-json-input]')).toBeTruthy();

		unmount(instance);
	});
});
