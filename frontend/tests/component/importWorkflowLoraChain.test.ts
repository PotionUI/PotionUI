// @vitest-environment jsdom
//
// The "LoRA chain" card on the Form step's workflow-inputs column: rendered
// when analyze detects a chain, "Convert to LoRA picker" seeds a lora_picker
// field from the non-kept-fixed nodes, "Keep fixed" excludes a node from
// that conversion, and a converted node's candidates disappear from the
// mappable workflow-inputs list. Drives the REAL compiled comfyui-backend
// plugin dist (see importWorkflowFormStep.test.ts for the harness pattern).
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

const LORA_CHAIN = {
	source_node_id: '4',
	target_node_id: '3',
	lora_node_ids: ['101', '102'],
	has_clip_path: false,
	nodes: [
		{ node_id: '101', class_type: 'LoraLoaderModelOnly', lora_name: 'style_a.safetensors', strength_model: 0.8, strength_clip: null },
		{ node_id: '102', class_type: 'LoraLoaderModelOnly', lora_name: 'style_b.safetensors', strength_model: 0.6, strength_clip: null }
	]
};

const ANALYZE_RESULT = {
	mode: 'txt2img',
	node_count: 6,
	sampler_node_id: '3',
	lora_chain: LORA_CHAIN,
	format: 'api',
	object_info_used: false,
	candidates: [
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'cfg', current_value: 6.0, value_type: 'float', suggested_field_type: 'slider', suggested_field_name: 'cfg', suggested_label: 'CFG Scale', suggested_config: {}, role: 'cfg' },
		{ node_id: '101', class_type: 'LoraLoaderModelOnly', node_title: 'LoRA 1', input_name: 'lora_name', current_value: 'style_a.safetensors', value_type: 'str', suggested_field_type: 'lora_picker', suggested_field_name: 'loras', suggested_label: 'LoRAs', suggested_config: {}, role: 'lora_slot' },
		{ node_id: '102', class_type: 'LoraLoaderModelOnly', node_title: 'LoRA 2', input_name: 'lora_name', current_value: 'style_b.safetensors', value_type: 'str', suggested_field_type: 'lora_picker', suggested_field_name: 'loras', suggested_label: 'LoRAs', suggested_config: {}, role: 'lora_slot' }
	],
	default_form: { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] },
	default_history: []
};

const THREE_NODE_LORA_CHAIN = {
	source_node_id: '4',
	target_node_id: '3',
	lora_node_ids: ['101', '102', '103'],
	has_clip_path: false,
	nodes: [
		{ node_id: '101', class_type: 'LoraLoaderModelOnly', lora_name: 'a.safetensors', strength_model: 1, strength_clip: null },
		{ node_id: '102', class_type: 'LoraLoaderModelOnly', lora_name: 'b.safetensors', strength_model: 1, strength_clip: null },
		{ node_id: '103', class_type: 'LoraLoaderModelOnly', lora_name: 'c.safetensors', strength_model: 1, strength_clip: null }
	]
};

const ANALYZE_RESULT_THREE_NODE_CHAIN = {
	...ANALYZE_RESULT,
	lora_chain: THREE_NODE_LORA_CHAIN,
	candidates: [
		...ANALYZE_RESULT.candidates,
		{ node_id: '103', class_type: 'LoraLoaderModelOnly', node_title: 'LoRA 3', input_name: 'lora_name', current_value: 'c.safetensors', value_type: 'str', suggested_field_type: 'lora_picker', suggested_field_name: 'loras', suggested_label: 'LoRAs', suggested_config: {}, role: 'lora_slot' }
	]
};

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-lora-chain-${Math.random().toString(36).slice(2)}.mjs`);
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

describe('ImportWorkflowTab LoRA chain card (real compiled dist)', () => {
	it('renders one row per detected chain node', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		const card = el.querySelector('[data-import-lora-chain]')!;
		expect(card).toBeTruthy();
		expect(card.querySelector('[data-lora-chain-node="101"]')?.textContent).toContain('style_a.safetensors');
		expect(card.querySelector('[data-lora-chain-node="102"]')?.textContent).toContain('style_b.safetensors');

		unmount(instance);
	});

	it('Convert to LoRA picker adds the field seeded with both LoRAs in chain order', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();

		const card = el.querySelector('[data-field-name="loras"]')!;
		expect(card).toBeTruthy();
		expect((card.querySelector('.di-field-type') as HTMLSelectElement).value).toBe('lora_picker');
		expect((card.querySelector('.di-field-default') as HTMLInputElement).value).toContain('2 item');

		// Both rows are now marked replaced, and the action button is disabled.
		expect(el.querySelectorAll('[data-import-lora-chain] .chip-info').length).toBe(2);
		expect(el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.disabled).toBe(true);

		unmount(instance);
	});

	it('Keep fixed excludes a node from the picker conversion', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-lora-chain-node="101"] [data-action="lora-keep-fixed"]')!.click();
		await settle();

		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();

		// Only node 102 is marked "→ picker"; node 101 stays unmarked (kept).
		const row101 = el.querySelector('[data-lora-chain-node="101"]')!;
		const row102 = el.querySelector('[data-lora-chain-node="102"]')!;
		expect(row101.querySelector('.chip-info')).toBeFalsy();
		expect(row102.querySelector('.chip-info')).toBeTruthy();

		unmount(instance);
	});

	it('candidates for a replaced node disappear from the workflow-inputs list, but a kept node stays mappable', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		// Keep 101 fixed, convert (only 102 gets replaced).
		el.querySelector<HTMLButtonElement>('[data-lora-chain-node="101"] [data-action="lora-keep-fixed"]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();

		// 102's lora_name candidate is gone from the mappable inputs list...
		expect(el.querySelector('[data-input-key="102:lora_name"]')).toBeFalsy();
		// ...but 101 (kept fixed) is still there, individually mappable.
		expect(el.querySelector('[data-input-key="101:lora_name"]')).toBeTruthy();

		unmount(instance);
	});

	it('converting again is a no-op', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();

		expect(el.querySelectorAll('[data-field-name="loras"]')).toHaveLength(1);

		unmount(instance);
	});

	it('removing the picker field un-marks the chain rows', async () => {
		const el = target();
		const instance = await mountOnFormStep(el);

		el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.click();
		await settle();
		expect(el.querySelectorAll('[data-import-lora-chain] .chip-info').length).toBe(2);

		el.querySelector('[data-field-name="loras"]')!.querySelector<HTMLButtonElement>('[data-action="remove"]')!.click();
		await settle();

		expect(el.querySelectorAll('[data-import-lora-chain] .chip-info').length).toBe(0);
		expect(el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.disabled).toBe(false);
		// Both rows are individually mappable again.
		expect(el.querySelector('[data-input-key="101:lora_name"]')).toBeTruthy();
		expect(el.querySelector('[data-input-key="102:lora_name"]')).toBeTruthy();

		unmount(instance);
	});

	it('keeping the middle node of a three-node chain fixed shows an inline error and disables Convert', async () => {
		const el = target();
		const instance = await mountOnFormStep(el, ANALYZE_RESULT_THREE_NODE_CHAIN);

		// 101 and 103 stay destined for the picker; keeping 102 (the middle
		// node) fixed sandwiches it between two to-be-replaced nodes.
		el.querySelector<HTMLButtonElement>('[data-lora-chain-node="102"] [data-action="lora-keep-fixed"]')!.click();
		await settle();

		const error = el.querySelector('[data-lora-sandwich-error]');
		expect(error).toBeTruthy();
		expect(error?.textContent).toContain('Kept LoRA node 102 sits between replaced nodes 101 and 103');
		expect(el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.disabled).toBe(true);

		unmount(instance);
	});

	it('bite check: keeping an end node of the three-node chain fixed is not sandwiched', async () => {
		const el = target();
		const instance = await mountOnFormStep(el, ANALYZE_RESULT_THREE_NODE_CHAIN);

		el.querySelector<HTMLButtonElement>('[data-lora-chain-node="101"] [data-action="lora-keep-fixed"]')!.click();
		await settle();

		expect(el.querySelector('[data-lora-sandwich-error]')).toBeFalsy();
		expect(el.querySelector<HTMLButtonElement>('[data-action="convert-lora-picker"]')!.disabled).toBe(false);

		unmount(instance);
	});
});
