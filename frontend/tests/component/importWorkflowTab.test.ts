// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist (built by
// scripts/build-plugins.mjs) through the host runtime the way the core
// admin plugin-detail tab strip does (resolvePluginComponent ->
// _wrapPluginDistComponent), mirroring pluginDistHostMount.test.ts's
// stage/load approach. Exercises the 4-step wizard end to end: paste a
// workflow -> Continue (analyze, mocked) -> step 2 candidate rows (obvious
// pre-ticked, the rest collapsed under "More inputs") -> Continue
// (requirements preview, mocked) -> step 3 requirement rows -> Continue
// (create, mocked) -> step 4 lint result -> "Open in Presets" link.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount, flushSync } from 'svelte';
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
		{
			node_id: '3',
			class_type: 'KSampler',
			node_title: 'KSampler',
			input_name: 'seed',
			current_value: 619589674328597,
			value_type: 'int',
			suggested_field_type: 'seed',
			suggested_field_name: 'seed',
			suggested_label: 'Seed',
			suggested_config: {},
			role: 'seed',
			obvious: true
		},
		{
			node_id: '3',
			class_type: 'KSampler',
			node_title: 'KSampler',
			input_name: 'steps',
			current_value: 27,
			value_type: 'int',
			suggested_field_type: 'slider',
			suggested_field_name: 'steps',
			suggested_label: 'Steps',
			suggested_config: { min: 1, max: 150, step: 1 },
			role: 'steps',
			obvious: true
		},
		{
			node_id: '3',
			class_type: 'KSampler',
			node_title: 'KSampler',
			input_name: 'sampler_name',
			current_value: 'euler',
			value_type: 'str',
			suggested_field_type: 'select',
			suggested_field_name: 'sampler_name',
			suggested_label: 'Sampler',
			suggested_config: {},
			role: 'sampler',
			obvious: false
		},
		// Resolution is always a width+height pair sharing one node - the wizard
		// must render this as ONE row, not two (see suggest.py's "Resolution +
		// batch size" loop).
		{
			node_id: '5',
			class_type: 'EmptyLatentImage',
			node_title: 'Empty Latent Image',
			input_name: 'width',
			current_value: 832,
			value_type: 'int',
			suggested_field_type: 'resolution',
			suggested_field_name: 'resolution',
			suggested_label: 'Image Resolution',
			suggested_config: {},
			role: 'resolution_width',
			obvious: true
		},
		{
			node_id: '5',
			class_type: 'EmptyLatentImage',
			node_title: 'Empty Latent Image',
			input_name: 'height',
			current_value: 1216,
			value_type: 'int',
			suggested_field_type: 'resolution',
			suggested_field_name: 'resolution',
			suggested_label: 'Image Resolution',
			suggested_config: {},
			role: 'resolution_height',
			obvious: true
		}
	]
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
	const staged = resolve(STAGE_DIR, basename(DIST_PATH));
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
	it('walks the full wizard: analyze -> inputs -> requirements -> create -> done', async () => {
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') {
				return jsonResponse({
					success: true,
					data: [
						{ type: 'seed', container: false },
						{ type: 'slider', container: false },
						{ type: 'select', container: false },
						{ type: 'resolution', container: false }
					]
				});
			}
			if (url === '/api/plugins/comfyui-backend/presets/families') {
				return jsonResponse({ families: ['SDXL', 'Flux1'] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				return jsonResponse(ANALYZE_RESULT);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') {
				return jsonResponse(REQUIREMENTS_RESULT);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				const body = JSON.parse(String(init?.body));
				expect(body.model_family).toBe('SDXL');
				expect(body.display_name).toBe('My import');
				// Only the obvious candidates are ticked by default - width and
				// height both ride along even though the merged row is one checkbox.
				expect(body.fields).toHaveLength(4);
				expect(body.fields.map((f: any) => f.input_name).sort()).toEqual(['height', 'seed', 'steps', 'width']);
				return jsonResponse(IMPORT_RESULT);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend', plugin: { id: 'comfyui-backend' } } });
		await settle();

		const wizard = el.querySelector('[data-import-wizard]');
		expect(wizard).toBeTruthy();

		// Step 1: Source.
		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]');
		expect(textarea).toBeTruthy();
		textarea!.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} } });
		textarea!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		const continueBtn = el.querySelector<HTMLButtonElement>('button[data-import-analyze]');
		expect(continueBtn!.disabled).toBe(false);
		continueBtn!.click();
		await settle();

		// Step 2: Inputs.
		expect(el.querySelector('[data-import-detected]')?.textContent).toContain('7 nodes');
		expect(el.querySelector('[data-import-format]')?.textContent).toBe('api');
		expect(el.querySelector('[data-wiz-step="inputs"]')?.className).toContain('current');

		let rows = el.querySelectorAll('[data-import-candidates] .candidate-row');
		expect(rows).toHaveLength(3);
		rows.forEach((row) => {
			expect(row.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
		});

		const resolutionRow = Array.from(rows).find((row) => row.textContent?.includes('width × height'));
		expect(resolutionRow, 'expected one merged width x height row').toBeTruthy();
		expect(resolutionRow!.textContent).toContain('832 × 1216');
		expect(resolutionRow!.querySelectorAll('input[type="checkbox"]')).toHaveLength(1);

		const moreToggle = el.querySelector<HTMLButtonElement>('.more-toggle');
		expect(moreToggle?.textContent).toContain('More inputs (1)');
		moreToggle!.click();
		await settle();
		rows = el.querySelectorAll('[data-import-candidates] .candidate-row');
		expect(rows).toHaveLength(4);

		const familyInput = el.querySelector<HTMLInputElement>('#import-model-family');
		familyInput!.value = 'SDXL';
		familyInput!.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = el.querySelector<HTMLInputElement>('#import-display-name');
		nameInput!.value = 'My import';
		nameInput!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		const continueInputsBtn = el.querySelector<HTMLButtonElement>('button[data-import-continue-inputs]');
		expect(continueInputsBtn!.disabled).toBe(false);
		continueInputsBtn!.click();
		await settle();

		// Step 3: Requirements (fetched automatically on entering the step).
		expect(el.querySelector('[data-wiz-step="requirements"]')?.className).toContain('current');
		const reqRows = el.querySelectorAll('[data-import-requirements] .req-row');
		expect(reqRows).toHaveLength(2);
		expect(el.querySelector('[data-import-requirements]')?.textContent).toContain('FaceDetailer');
		expect(el.querySelector('[data-import-requirements]')?.textContent).toContain('sdxlBase_v10.safetensors');
		const missingDot = Array.from(reqRows).find((r) => r.textContent?.includes('FaceDetailer'))!.querySelector('.req-dot');
		expect(missingDot?.className).toContain('missing');

		// Never blocks - Continue is enabled even with a missing requirement.
		const createBtn = el.querySelector<HTMLButtonElement>('button[data-import-create]');
		expect(createBtn!.disabled).toBe(false);
		createBtn!.click();
		await settle();

		// Step 4: Done.
		expect(el.querySelector('[data-wiz-step="done"]')?.className).toContain('current');
		const lint = el.querySelector('[data-import-lint]');
		expect(lint).toBeTruthy();
		expect(lint!.textContent).toContain('Lint clean');
		expect(lint!.textContent).toContain('content/presets/local/SDXL/imported');

		const openLink = el.querySelector<HTMLAnchorElement>('a[data-import-open-preset]');
		expect(openLink).toBeTruthy();
		expect(openLink!.getAttribute('href')).toBe('/admin?tab=presets&preset=PRESET123');

		unmount(instance);
	});

	it('shows the backend message verbatim when a UI-format workflow needs a reachable ComfyUI backend', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				return jsonResponse(
					{
						detail:
							'A reachable ComfyUI backend is needed to import UI-format workflows; use Export (API) or configure the backend'
					},
					false,
					400
				);
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
		expect(error?.textContent).toBe(
			'A reachable ComfyUI backend is needed to import UI-format workflows; use Export (API) or configure the backend'
		);
		// Still on step 1 - no candidate list rendered, rail hasn't advanced.
		expect(el.querySelector('[data-import-candidates]')).toBeNull();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		unmount(instance);
	});

	it('lets "Change workflow" reset back to step 1 from the Inputs step', async () => {
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

		expect(el.querySelector('[data-import-candidates]')).toBeTruthy();

		const changeLink = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes('Change workflow'));
		expect(changeLink).toBeTruthy();
		changeLink!.click();
		await settle();

		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');
		expect(el.querySelector('textarea[data-import-json-input]')).toBeTruthy();

		unmount(instance);
	});
});
