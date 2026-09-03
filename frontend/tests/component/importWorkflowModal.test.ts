// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist (built by
// scripts/build-plugins.mjs) through the host runtime the way `PluginSlot`
// does, mirroring `pluginDistHostMount.test.ts`'s stage/load approach: paste
// a workflow -> analyze (mocked) -> obvious candidates pre-ticked, the rest
// collapsed under "More inputs" -> create (mocked) -> lint result shown ->
// "Open in Presets" calls back into the host's `selectPreset`/`refreshPresets`.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount, flushSync } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowAction.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

const ANALYZE_RESULT = {
	mode: 'text2img',
	node_count: 7,
	sampler_node_id: '3',
	lora_chain: null,
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
		{
			node_id: '5',
			class_type: 'EmptyLatentImage',
			node_title: 'Empty Latent Image',
			input_name: 'width',
			current_value: 832,
			value_type: 'int',
			suggested_field_type: 'resolution',
			suggested_field_name: 'width',
			suggested_label: 'Resolution',
			suggested_config: {},
			role: 'resolution',
			obvious: false
		}
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

let ImportWorkflowAction: any;

beforeAll(async () => {
	if (!existsSync(resolve(REPO_ROOT, DIST_PATH))) {
		throw new Error(
			`${DIST_PATH} is missing - run \`node scripts/build-plugins.mjs comfyui-backend\` first.`
		);
	}
	const raw = await loadDist();
	ImportWorkflowAction = _wrapPluginDistComponent(raw);
});

afterEach(() => {
	vi.unstubAllGlobals();
	document.body.innerHTML = '';
});

describe('ImportWorkflowAction (real compiled dist)', () => {
	it('analyzes a pasted workflow, pre-ticks obvious candidates, creates the preset and opens it', async () => {
		const selectPreset = vi.fn();
		const refreshPresets = vi.fn().mockResolvedValue(undefined);

		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') {
				return jsonResponse({
					success: true,
					data: [
						{ type: 'seed', container: false },
						{ type: 'slider', container: false },
						{ type: 'select', container: false },
						{ type: 'resolution', container: false },
						{ type: 'textbox', container: false }
					]
				});
			}
			if (url === '/api/plugins/comfyui-backend/presets/families') {
				return jsonResponse({ families: ['SDXL', 'Flux1'] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				return jsonResponse(ANALYZE_RESULT);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				const body = JSON.parse(String(init?.body));
				expect(body.model_family).toBe('SDXL');
				expect(body.display_name).toBe('My import');
				// Only the two obvious candidates are ticked by default.
				expect(body.fields).toHaveLength(2);
				expect(body.fields.map((f: any) => f.input_name).sort()).toEqual(['seed', 'steps']);
				return jsonResponse(IMPORT_RESULT);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowAction, {
			target: el,
			props: { context: { selectPreset, refreshPresets } }
		});
		await settle();

		const trigger = el.querySelector<HTMLButtonElement>('button[aria-label="Import ComfyUI workflow"]');
		expect(trigger).toBeTruthy();
		trigger!.click();
		await settle();

		const dialog = document.querySelector('[role="dialog"][aria-label="Import ComfyUI workflow"]');
		expect(dialog).toBeTruthy();

		const textarea = dialog!.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]');
		expect(textarea).toBeTruthy();
		textarea!.value = JSON.stringify({ '3': { class_type: 'KSampler', inputs: {} } });
		textarea!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		const analyzeBtn = dialog!.querySelector<HTMLButtonElement>('button[data-import-analyze]');
		expect(analyzeBtn!.disabled).toBe(false);
		analyzeBtn!.click();
		await settle();

		expect(dialog!.querySelector('[data-import-detected]')?.textContent).toContain('text2img');
		expect(dialog!.querySelector('[data-import-detected]')?.textContent).toContain('7 nodes');

		// Obvious candidates (seed, steps) visible and pre-ticked; the other two
		// collapsed under "More inputs".
		let rows = dialog!.querySelectorAll('[data-import-candidates] .candidate-row');
		expect(rows).toHaveLength(2);
		rows.forEach((row) => {
			expect(row.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
		});

		const moreToggle = dialog!.querySelector<HTMLButtonElement>('.more-toggle');
		expect(moreToggle?.textContent).toContain('More inputs (2)');
		moreToggle!.click();
		await settle();
		rows = dialog!.querySelectorAll('[data-import-candidates] .candidate-row');
		expect(rows).toHaveLength(4);

		const familyInput = dialog!.querySelector<HTMLInputElement>('#import-model-family');
		familyInput!.value = 'SDXL';
		familyInput!.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = dialog!.querySelector<HTMLInputElement>('#import-display-name');
		nameInput!.value = 'My import';
		nameInput!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		const createBtn = dialog!.querySelector<HTMLButtonElement>('button[data-import-create]');
		expect(createBtn!.disabled).toBe(false);
		createBtn!.click();
		await settle();

		const lint = dialog!.querySelector('[data-import-lint]');
		expect(lint).toBeTruthy();
		expect(lint!.textContent).toContain('Lint clean');
		expect(lint!.textContent).toContain('content/presets/local/SDXL/imported');

		const openBtn = dialog!.querySelector<HTMLButtonElement>('button[data-import-open-preset]');
		expect(openBtn).toBeTruthy();
		openBtn!.click();
		await settle();

		expect(refreshPresets).toHaveBeenCalledTimes(1);
		expect(selectPreset).toHaveBeenCalledWith('PRESET123');
		// The modal closes itself after handing off to the host.
		expect(document.querySelector('[role="dialog"][aria-label="Import ComfyUI workflow"]')).toBeNull();

		unmount(instance);
	});

	it('shows the backend message when the pasted JSON is the UI-format export', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				return jsonResponse(
					{ detail: 'This looks like the UI workflow export - use "Export (API)" in ComfyUI instead.' },
					false,
					400
				);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowAction, { target: el, props: { context: {} } });
		await settle();

		el.querySelector<HTMLButtonElement>('button[aria-label="Import ComfyUI workflow"]')!.click();
		await settle();

		const dialog = document.querySelector('[role="dialog"][aria-label="Import ComfyUI workflow"]')!;
		const textarea = dialog.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ nodes: [], links: [] });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		dialog.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		const error = dialog.querySelector('[data-import-analyze-error]');
		expect(error?.textContent).toContain('Export (API)');
		// Still on the paste step - no candidate list rendered.
		expect(dialog.querySelector('[data-import-candidates]')).toBeNull();

		unmount(instance);
	});
});
