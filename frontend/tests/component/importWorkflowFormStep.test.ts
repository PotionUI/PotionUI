// @vitest-environment jsdom
//
// Focused coverage of the Form step's designer beyond the happy-path walk
// in importWorkflowTab.test.ts: adding a Row/Group container from the "Add"
// menu present at the end of every container, and the mapping editor
// (checkbox list of candidate inputs + a per-mapping transform select)
// opened from a field card.
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
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'sampler_name', current_value: 'euler', value_type: 'str', suggested_field_type: 'select', suggested_field_name: 'sampler_name', suggested_label: 'Sampler', suggested_config: {}, role: 'sampler' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'scheduler', current_value: 'normal', value_type: 'str', suggested_field_type: 'select', suggested_field_name: 'scheduler', suggested_label: 'Scheduler', suggested_config: {}, role: 'scheduler' },
		{ node_id: '9', class_type: 'LatentUpscale', node_title: 'LatentUpscale', input_name: 'width', current_value: 1024, value_type: 'int', suggested_field_type: 'number', suggested_field_name: 'upscale_width', suggested_label: 'Upscale width', suggested_config: {}, role: 'other' }
	],
	default_form: { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] },
	default_history: []
};

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-form-${Math.random().toString(36).slice(2)}.mjs`);
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
});

async function mountOnFormStep(el: HTMLDivElement) {
	const fetchMock = vi.fn(async (url: string) => {
		if (url === '/api/fields/types') return jsonResponse({ success: true, data: [{ type: 'select', container: false }, { type: 'number', container: false }] });
		if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
		if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(ANALYZE_RESULT);
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

describe('ImportWorkflowTab Form step (real compiled dist)', () => {
	it('starts empty from a tabs-less default_form and shows the empty-state message', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		expect(el.querySelector('[data-tab-id="generation"]')).toBeTruthy();
		expect(el.querySelector('[data-import-form-items]')?.textContent).toContain('Drop inputs here or click Add on the left');

		unmount(instance);
	});

	it('adds a Row container and a Group container from the root Add menu', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		const rootAddButton = el.querySelector<HTMLButtonElement>('[data-add-root="true"] [data-action="open-add-menu"]')!;
		rootAddButton.click();
		await settle();
		el.querySelector<HTMLButtonElement>('[data-add-kind="row"]')!.click();
		await settle();

		const row = el.querySelector('[data-item-kind="row"]')!;
		expect(row).toBeTruthy();
		expect(row.querySelector('.chip-violet')).toBeTruthy();
		row.querySelector<HTMLButtonElement>('[data-columns="3"]')!.click();
		await settle();
		expect(row.querySelector('[data-columns="3"]')?.className).toContain('active');

		rootAddButton.click();
		await settle();
		el.querySelector<HTMLButtonElement>('[data-add-kind="group"]')!.click();
		await settle();
		expect(el.querySelector('[data-item-kind="group"]')).toBeTruthy();

		// Row <-> Group convert swaps the chip.
		const group = el.querySelector('[data-item-kind="group"]')!;
		group.querySelector<HTMLButtonElement>('[data-action="convert"]')!.click();
		await settle();
		expect(el.querySelectorAll('[data-item-kind="row"]')).toHaveLength(2);

		unmount(instance);
	});

	it('opens the mapping editor on a field and edits which inputs it maps plus their transform', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-input-key="3:sampler_name"] [data-action="add-input"]')!.click();
		await settle();

		const card = el.querySelector('[data-field-name="sampler_name"]')!;
		card.querySelector<HTMLButtonElement>('[data-action="toggle-mapping"]')!.click();
		await settle();

		const editor = card.querySelector('[data-mapping-editor]')!;
		expect(editor).toBeTruthy();
		const schedulerRow = editor.querySelector('[data-mapedit-key="3:scheduler"]')!;
		expect(schedulerRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(false);

		schedulerRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle();
		expect(card.querySelector('.di-mapping')?.textContent).toContain('3.inputs.scheduler');

		const transformSelect = schedulerRow.querySelector<HTMLSelectElement>('.di-mapedit-transform select')!;
		transformSelect.value = 'strip_model_prefix';
		transformSelect.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();

		// Unchecking removes the mapping again.
		schedulerRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle();
		expect(card.querySelector('.di-mapping')?.textContent).not.toContain('scheduler');

		unmount(instance);
	});
});
