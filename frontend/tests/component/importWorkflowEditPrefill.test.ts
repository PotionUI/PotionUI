// @vitest-environment jsdom
//
// Drives the REAL compiled ImportWorkflowTab dist through the "edit" entry
// point: a pending preset id in sessionStorage (as ImportedPresetsTab's edit
// action leaves it) makes the wizard fetch .../source on mount, land on step
// 1 already showing the loaded workflow, prefill family/variant/display
// name and the sidecar's exact field choices, and send `overwrite_preset_id`
// on submit instead of creating a new preset.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount, flushSync } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

const SOURCE_RESPONSE = {
	workflow: { '3': { class_type: 'KSampler', inputs: { seed: 619589674328597 } } },
	mode: 'text2img',
	node_count: 5,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	model_family: 'SDXL',
	variant: 'imported',
	display_name: 'SDXL — My workflow',
	sidecar_choices: [{ node_id: '3', input_name: 'seed', field_name: 'seed', field_type: 'seed', label: 'Custom Seed' }],
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
			current_value: 20,
			value_type: 'int',
			suggested_field_type: 'slider',
			suggested_field_name: 'steps',
			suggested_label: 'Steps',
			suggested_config: {},
			role: 'steps',
			obvious: false
		}
	]
};

const UPDATE_RESULT = {
	preset_id: 'EXISTING-ID',
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
	sessionStorage.clear();
});

describe('ImportWorkflowTab edit prefill (real compiled dist)', () => {
	it('loads the source on mount, prefills identity + sidecar choices, and updates in place on submit', async () => {
		sessionStorage.setItem('comfyui-import-edit-preset-id', 'EXISTING-ID');

		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/imported/EXISTING-ID/source') {
				return jsonResponse(SOURCE_RESPONSE);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') {
				return jsonResponse({ results: [] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				const body = JSON.parse(String(init?.body));
				expect(body.overwrite_preset_id).toBe('EXISTING-ID');
				expect(body.model_family).toBe('SDXL');
				expect(body.display_name).toBe('SDXL — My workflow');
				return jsonResponse(UPDATE_RESULT);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		// The sessionStorage handoff is consumed once - gone after mount.
		expect(sessionStorage.getItem('comfyui-import-edit-preset-id')).toBeNull();

		// Step 1 shows the loaded-source card, not an empty textarea.
		expect(el.querySelector('textarea[data-import-json-input]')).toBeNull();
		const editBanner = el.querySelector('[data-import-editing]');
		expect(editBanner?.textContent).toContain('SDXL — My workflow');
		expect(el.querySelector('[data-import-detected]')?.textContent).toContain('5 nodes');

		const continueBtn = el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!;
		expect(continueBtn.disabled).toBe(false);
		continueBtn.click();
		await settle();

		// Step 2: the sidecar's seed choice is ticked with its stored label,
		// even though the candidate itself isn't "obvious" by default.
		const rows = el.querySelectorAll('[data-import-candidates] .candidate-row');
		const seedRow = Array.from(rows).find((r) => r.textContent?.includes('seed'))!;
		expect(seedRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
		expect(seedRow.querySelector<HTMLInputElement>('.candidate-label')!.value).toBe('Custom Seed');

		expect(el.querySelector<HTMLInputElement>('#import-model-family')!.value).toBe('SDXL');
		expect(el.querySelector<HTMLInputElement>('#import-display-name')!.value).toBe('SDXL — My workflow');

		el.querySelector<HTMLButtonElement>('button[data-import-continue-inputs]')!.click();
		await settle();

		const updateBtn = el.querySelector<HTMLButtonElement>('button[data-import-create]')!;
		expect(updateBtn.textContent).toContain('Update preset');
		updateBtn.click();
		await settle();

		expect(el.querySelector('[data-import-lint]')).toBeTruthy();
		unmount(instance);
	});

	it('does nothing special without a pending sessionStorage edit request', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		expect(el.querySelector('textarea[data-import-json-input]')).toBeTruthy();
		expect(el.querySelector('[data-import-editing]')).toBeNull();

		unmount(instance);
	});
});
