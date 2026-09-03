// @vitest-environment jsdom
//
// Focused coverage of the History step: toggling a row's emit checkbox,
// changing its value format, reordering rows up/down (mirrored by the live
// preview), and the exact `history` payload shape sent to /presets/import.
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
	node_count: 3,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	candidates: [],
	default_form: {
		tabs: [
			{
				id: 'generation',
				label: 'Generation',
				icon: null,
				items: [
					{ kind: 'field', field_name: 'steps', field_type: 'slider', label: 'Steps', default: 24, config: null, mappings: [{ node_id: '3', input_name: 'steps', transform: 'none' }] },
					{ kind: 'field', field_name: 'cfg', field_type: 'number', label: 'CFG Scale', default: 3.5, config: null, mappings: [{ node_id: '3', input_name: 'cfg', transform: 'none' }] }
				]
			}
		]
	},
	default_history: [{ field: 'steps', label: 'Steps', format: 'number', template: null }]
};

const IMPORT_RESULT = { preset_id: 'PRESET1', path: 'content/presets/local/SDXL/imported', mode: 'text2img', lint: { errors: [], warnings: [] } };

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-history-${Math.random().toString(36).slice(2)}.mjs`);
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
let fetchMock: ReturnType<typeof vi.fn>;

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

async function mountOnHistoryStep(el: HTMLDivElement, importAssert?: (body: any) => void) {
	fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
		if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
		if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
		if (url === '/api/plugins/comfyui-backend/presets/import/analyze') return jsonResponse(ANALYZE_RESULT);
		if (url === '/api/plugins/comfyui-backend/presets/import/requirements') return jsonResponse({ results: [] });
		if (url === '/api/plugins/comfyui-backend/presets/import') {
			const body = JSON.parse(String(init?.body));
			importAssert?.(body);
			return jsonResponse(IMPORT_RESULT);
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

	const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
	familyInput.value = 'SDXL';
	familyInput.dispatchEvent(new Event('input', { bubbles: true }));
	const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
	nameInput.value = 'My import';
	nameInput.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();

	el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
	await settle();

	return instance;
}

describe('ImportWorkflowTab History step (real compiled dist)', () => {
	it('toggles emit, changes the value format, and reorders rows - the preview follows both', async () => {
		const el = target();
		const instance = await mountOnHistoryStep(el);

		const preview = () => el.querySelector('[data-history-preview]')!;
		expect(preview().textContent).toContain('Steps');
		expect(preview().textContent).not.toContain('CFG Scale');

		// Toggle cfg on - it joins the preview.
		const cfgRow = el.querySelector('[data-history-row="cfg"]')!;
		cfgRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle();
		expect(preview().textContent).toContain('CFG Scale');

		// Switch cfg's format to Custom Jinja - a template row appears with a
		// live-rendered value.
		const cfgFormat = cfgRow.querySelector<HTMLSelectElement>('select.hist-value-select')!;
		cfgFormat.value = 'jinja';
		cfgFormat.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		const jinjaInput = cfgRow.querySelector<HTMLInputElement>('.hist-jinja-input')!;
		jinjaInput.value = '{{ form.cfg }} strength';
		jinjaInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(cfgRow.querySelector('.hist-jinja-out')?.textContent).toContain('3.5 strength');

		// Reorder: move cfg above steps, preview cell order follows.
		cfgRow.querySelector<HTMLButtonElement>('[data-action="move-up"]')!.click();
		await settle();
		const rows = Array.from(el.querySelectorAll('[data-history-table] [data-history-row]'));
		expect(rows.map((r) => r.getAttribute('data-history-row'))).toEqual(['cfg', 'steps']);

		const previewLabels = Array.from(preview().querySelectorAll('.preview-k')).map((n) => n.textContent);
		expect(previewLabels.indexOf('CFG Scale')).toBeLessThan(previewLabels.indexOf('Steps'));

		unmount(instance);
	});

	it('sends only the enabled rows, in table order, on create', async () => {
		const el = target();
		let sentHistory: any = null;
		const instance = await mountOnHistoryStep(el, (body) => {
			sentHistory = body.history;
		});

		const cfgRow = el.querySelector('[data-history-row="cfg"]')!;
		cfgRow.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click();
		await settle();
		cfgRow.querySelector<HTMLButtonElement>('[data-action="move-up"]')!.click();
		await settle();

		const cfgLabel = cfgRow.querySelector<HTMLInputElement>('.hist-label-input')!;
		cfgLabel.value = 'CFG';
		cfgLabel.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();

		expect(sentHistory).toEqual([
			{ field: 'cfg', label: 'CFG', format: 'as_is', template: null },
			{ field: 'steps', label: 'Steps', format: 'number', template: null }
		]);

		unmount(instance);
	});
});
