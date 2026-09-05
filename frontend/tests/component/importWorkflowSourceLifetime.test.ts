// @vitest-environment jsdom
//
// A selected file's FileReader, and the analyze / edit-source / requirements
// / create fetches, used to write their result on arrival with no ownership
// of the source they belonged to. Picking file A then B and letting A's
// FileReader complete last left A's JSON as the source; retiring the current
// source (a new file, "Change workflow", "Import another") while a
// requirements preview was still in flight let that stale response
// repopulate - or silently block re-fetching for - whatever replaced it.
// This drives the real compiled dist end to end (mirrors
// importWorkflowTab.test.ts) so the fix is exercised through the actual
// wizard, not a mocked boundary.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

function emptyAnalyzeResult(nodeCount: number) {
	return {
		mode: 'text2img',
		node_count: nodeCount,
		sampler_node_id: '1',
		lora_chain: null,
		format: 'api',
		object_info_used: false,
		candidates: [],
		default_form: { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] },
		default_history: []
	};
}

const ANALYZE_A = emptyAnalyzeResult(5);
const ANALYZE_B = emptyAnalyzeResult(6);
const REQUIREMENTS_STALE = { results: [{ type: 'comfyui_node', name: 'StaleFromA', status: 'missing', detail: 'stale', hint: null }] };
const REQUIREMENTS_FRESH = { results: [{ type: 'comfyui_node', name: 'FreshForB', status: 'ok', detail: 'fresh', hint: null }] };
const IMPORT_RESULT = { preset_id: 'PRESET1', path: 'content/presets/local/SDXL/imported', mode: 'text2img', lint: { errors: [], warnings: [] } };

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-lifetime-${Math.random().toString(36).slice(2)}.mjs`);
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
// macrotask under jsdom (no native requestAnimationFrame).
async function settle() {
	await new Promise((r) => setTimeout(r, 0));
	await new Promise((r) => setTimeout(r, 0));
}

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400) {
	return { ok, status, json: async () => body } as Response;
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

function clickByText(el: HTMLElement, text: string) {
	const btn = Array.from(el.querySelectorAll('button')).find((b) => b.textContent?.includes(text));
	if (!btn) throw new Error(`No button with text "${text}"`);
	btn.click();
}

async function fillSourceAndAnalyze(el: HTMLElement, workflow: unknown) {
	const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
	textarea.value = JSON.stringify(workflow);
	textarea.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
	el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
	await settle();
}

async function fillFormAndContinue(el: HTMLElement, family: string, name: string) {
	const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
	familyInput.value = family;
	familyInput.dispatchEvent(new Event('input', { bubbles: true }));
	const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
	nameInput.value = name;
	nameInput.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
	el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
	await settle();
}

// A controllable stand-in for the browser's FileReader: readAsText does
// nothing on its own, letting the test fire onload for two instances in
// whatever order it wants.
class FakeFileReader {
	static instances: FakeFileReader[] = [];
	onload: ((e: { target: { result: string } }) => void) | null = null;
	onerror: (() => void) | null = null;
	result: string | null = null;
	constructor() {
		FakeFileReader.instances.push(this);
	}
	readAsText(_file: unknown) {
		// intentionally inert - see complete()
	}
	complete(text: string) {
		this.result = text;
		this.onload?.({ target: { result: text } });
	}
}

function selectFile(input: HTMLInputElement, filename: string) {
	const file = new File(['irrelevant - FakeFileReader ignores actual file content'], filename, { type: 'application/json' });
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input.dispatchEvent(new Event('change', { bubbles: true }));
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
	FakeFileReader.instances = [];
});

describe('ImportWorkflowTab source lifetime (real compiled dist)', () => {
	it('keeps the second selected file even when its FileReader completes before the first one', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('FileReader', FakeFileReader);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const fileInput = el.querySelector<HTMLInputElement>('input[type="file"]')!;
		selectFile(fileInput, 'file-a.json');
		await settle();
		selectFile(fileInput, 'file-b.json');
		await settle();
		expect(FakeFileReader.instances).toHaveLength(2);

		// Reversed completion order: B (the second selection, and the one
		// that should win) finishes reading before A does.
		FakeFileReader.instances[1].complete('{"source":"b"}');
		await settle();
		FakeFileReader.instances[0].complete('{"source":"a"}');
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		expect(textarea.value).toBe('{"source":"b"}');

		unmount(instance);
	});

	it('retires a pending requirements preview on source change: the stale response is dropped and the new source still gets fetched', async () => {
		const requirementsCalls: Array<ReturnType<typeof deferred<Response>>> = [];
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				const body = JSON.parse(String(init?.body));
				return jsonResponse(body.workflow.marker === 'A' ? ANALYZE_A : ANALYZE_B);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') {
				const d = deferred<Response>();
				requirementsCalls.push(d);
				return d.promise;
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		await fillSourceAndAnalyze(el, { marker: 'A' });
		await fillFormAndContinue(el, 'SDXL', 'Import A');
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();

		expect(el.querySelector('[data-wiz-step="requirements"]')?.className).toContain('current');
		expect(requirementsCalls).toHaveLength(1); // A's request in flight, unresolved.

		// Retire A: back to the Form step, then "Change workflow" to step 1.
		clickByText(el, 'Back');
		await settle();
		clickByText(el, 'Back');
		await settle();
		expect(el.querySelector('[data-wiz-step="form"]')?.className).toContain('current');
		clickByText(el, 'Change workflow');
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		await fillSourceAndAnalyze(el, { marker: 'B' });
		await fillFormAndContinue(el, 'SDXL', 'Import B');
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();

		// B's own requirements request must fire - not skipped because A's
		// requirementsLoading/requirementsResults looked "already fetched".
		expect(requirementsCalls).toHaveLength(2);

		// A's stale response lands late - must not populate B's screen.
		requirementsCalls[0].resolve(jsonResponse(REQUIREMENTS_STALE));
		await settle();
		expect(el.textContent).not.toContain('StaleFromA');
		expect(el.querySelector('.req-loading')).toBeTruthy();

		// B's own response lands - this is what should render.
		requirementsCalls[1].resolve(jsonResponse(REQUIREMENTS_FRESH));
		await settle();
		const rows = Array.from(el.querySelectorAll('[data-import-requirements] .req-row'));
		expect(rows).toHaveLength(1);
		expect(rows[0].textContent).toContain('FreshForB');

		unmount(instance);
	});

	it('"Import another" retires a still-pending requirements preview so its late response cannot block or repopulate the reset wizard', async () => {
		const requirementsCalls: Array<ReturnType<typeof deferred<Response>>> = [];
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				const body = JSON.parse(String(init?.body));
				return jsonResponse(body.workflow.marker === 'A' ? ANALYZE_A : ANALYZE_B);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') {
				const d = deferred<Response>();
				requirementsCalls.push(d);
				return d.promise;
			}
			if (url === '/api/plugins/comfyui-backend/presets/import') return jsonResponse(IMPORT_RESULT);
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		await fillSourceAndAnalyze(el, { marker: 'A' });
		await fillFormAndContinue(el, 'SDXL', 'Import A');
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();
		expect(requirementsCalls).toHaveLength(1); // still pending when Create is clicked below.

		// The Create button is only gated on `creating`, not on the
		// requirements preview - clicking through while it's still loading
		// is a real path, not a test artifact.
		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();
		expect(el.querySelector('[data-wiz-step="done"]')?.className).toContain('current');

		clickByText(el, 'Import another');
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		// A's requirements preview finally resolves after the reset.
		requirementsCalls[0].resolve(jsonResponse(REQUIREMENTS_STALE));
		await settle();

		// Walk a fresh source (B) through to the requirements step again.
		await fillSourceAndAnalyze(el, { marker: 'B' });
		await fillFormAndContinue(el, 'SDXL', 'Import B');
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();

		// If A's late write had gone unguarded it would have left
		// requirementsResults non-null, and the old `if (requirementsResults
		// || requirementsLoading) return;` guard would have skipped
		// re-fetching entirely, showing A's stale row under B's wizard.
		expect(requirementsCalls).toHaveLength(2);
		expect(el.textContent).not.toContain('StaleFromA');

		requirementsCalls[1].resolve(jsonResponse(REQUIREMENTS_FRESH));
		await settle();
		const rows = Array.from(el.querySelectorAll('[data-import-requirements] .req-row'));
		expect(rows).toHaveLength(1);
		expect(rows[0].textContent).toContain('FreshForB');

		unmount(instance);
	});

	it('releases the stuck `analyzing` flag when a file is picked mid-analyze, and Continue re-analyzes the newly selected file', async () => {
		const analyzeCalls: Array<{ resolve: (v: Response) => void; body: any }> = [];
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				const d = deferred<Response>();
				analyzeCalls.push({ resolve: d.resolve, body: JSON.parse(String(init?.body)) });
				return d.promise;
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('FileReader', FakeFileReader);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		const continueBtn = el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!;

		textarea.value = JSON.stringify({ marker: 'A' });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		continueBtn.click();
		await settle();
		expect(analyzeCalls).toHaveLength(1);
		expect(continueBtn.disabled).toBe(true); // "Analyzing..."

		// Pick a file (B) while A's analyze is still in flight.
		const fileInput = el.querySelector<HTMLInputElement>('input[type="file"]')!;
		selectFile(fileInput, 'file-b.json');
		await settle();

		// Continue must be usable again right away - not stuck waiting on a
		// request for a source the user has already moved past.
		expect(continueBtn.disabled).toBe(false);

		// A's analyze resolves late - must not advance past step 1.
		analyzeCalls[0].resolve(jsonResponse(ANALYZE_A));
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');

		FakeFileReader.instances[0].complete(JSON.stringify({ marker: 'B' }));
		await settle();
		expect(textarea.value).toBe(JSON.stringify({ marker: 'B' }));

		continueBtn.click();
		await settle();
		expect(analyzeCalls).toHaveLength(2);
		expect(analyzeCalls[1].body.workflow.marker).toBe('B');

		unmount(instance);
	});

	it('releases the stuck `analyzing` flag when new text is pasted mid-analyze, and Continue analyzes the pasted text', async () => {
		const analyzeCalls: Array<{ resolve: (v: Response) => void; body: any }> = [];
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				const d = deferred<Response>();
				analyzeCalls.push({ resolve: d.resolve, body: JSON.parse(String(init?.body)) });
				return d.promise;
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		const continueBtn = el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!;

		textarea.value = JSON.stringify({ marker: 'A' });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		continueBtn.click();
		await settle();
		expect(analyzeCalls).toHaveLength(1);
		expect(continueBtn.disabled).toBe(true);

		// Paste B over A while A's analyze is still in flight.
		textarea.value = JSON.stringify({ marker: 'B' });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(continueBtn.disabled).toBe(false);

		// A's analyze resolves late - must not advance past step 1, and must
		// not clobber the pasted text.
		analyzeCalls[0].resolve(jsonResponse(ANALYZE_A));
		await settle();
		expect(el.querySelector('[data-wiz-step="source"]')?.className).toContain('current');
		expect(textarea.value).toBe(JSON.stringify({ marker: 'B' }));

		continueBtn.click();
		await settle();
		expect(analyzeCalls).toHaveLength(2);
		expect(analyzeCalls[1].body.workflow.marker).toBe('B');

		unmount(instance);
	});

	it('a paste after picking a file wins over that file\'s still-pending read', async () => {
		const analyzeCalls: Array<{ resolve: (v: Response) => void; body: any }> = [];
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				const d = deferred<Response>();
				analyzeCalls.push({ resolve: d.resolve, body: JSON.parse(String(init?.body)) });
				return d.promise;
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('FileReader', FakeFileReader);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const fileInput = el.querySelector<HTMLInputElement>('input[type="file"]')!;
		selectFile(fileInput, 'file-a.json');
		await settle();
		expect(FakeFileReader.instances).toHaveLength(1);

		// Paste B directly into the textarea before A's read completes.
		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = JSON.stringify({ marker: 'B' });
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		// A's still-pending read now completes - must not clobber the paste.
		FakeFileReader.instances[0].complete(JSON.stringify({ marker: 'A-file' }));
		await settle();
		expect(textarea.value).toBe(JSON.stringify({ marker: 'B' }));

		const continueBtn = el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!;
		continueBtn.click();
		await settle();
		expect(analyzeCalls).toHaveLength(1);
		expect(analyzeCalls[0].body.workflow.marker).toBe('B');

		unmount(instance);
	});
});
