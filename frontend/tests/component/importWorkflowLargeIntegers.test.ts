// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist through the large-
// integer transport contract (backend/api.py's `workflow_text`/
// `_make_json_safe`, docs/presets.md's "Exact large integers" note): the
// exact pasted/uploaded/stored text must reach analyze/requirements/import
// as `workflow_text`, untouched by this app's own JSON.parse/JSON.stringify,
// and a response value the backend had to tag (`{__exact_int__: "..."}`)
// because it can't survive a browser's JSON.parse as a Number must render
// correctly, never be offered as an editable mapped field, and never reach
// the posted form at all (the server refuses a field with no mappings).
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportWorkflowTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

// A plain JS string literal - the digit runs below are just characters,
// never parsed as a Number by the JS engine, so this constant is exact
// regardless of what the app under test does with it. Mirrors the lead's own
// repro value (2**53+1) and a uint64-max value.
const JUST_OVER_SAFE = '9007199254740993';
const UINT64_MAX = '18446744073709551615';
const RAW_WORKFLOW_TEXT =
	'{"3":{"class_type":"KSampler","inputs":{"seed":' +
	UINT64_MAX +
	',"steps":' +
	JUST_OVER_SAFE +
	',"cfg":3.5,"sampler_name":"euler","scheduler":"normal","denoise":1.0}},' +
	'"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"sdxlBase_v10.safetensors"}},' +
	'"5":{"class_type":"EmptyLatentImage","inputs":{"width":832,"height":1216,"batch_size":1}},' +
	'"6":{"class_type":"CLIPTextEncode","inputs":{"text":"a cat","clip":["4",1]}},' +
	'"7":{"class_type":"CLIPTextEncode","inputs":{"text":"bad","clip":["4",1]}},' +
	'"8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},' +
	'"9":{"class_type":"SaveImage","inputs":{"images":["8",0],"filename_prefix":"ComfyUI"}}}';

const ANALYZE_RESULT = {
	mode: 'text2img',
	node_count: 7,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	schema_fingerprint: 'fp-1',
	candidates: [
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'seed', current_value: { __exact_int__: UINT64_MAX }, value_type: 'int', suggested_field_type: 'seed', suggested_field_name: 'seed', suggested_label: 'Seed', suggested_config: {}, role: 'seed' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'steps', current_value: { __exact_int__: JUST_OVER_SAFE }, value_type: 'int', suggested_field_type: 'slider', suggested_field_name: 'steps', suggested_label: 'Steps', suggested_config: { min: 1, max: 150, step: 1 }, role: 'steps' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'cfg', current_value: 3.5, value_type: 'float', suggested_field_type: 'number', suggested_field_name: 'cfg', suggested_label: 'CFG Scale', suggested_config: {}, role: 'cfg' }
	],
	// "steps" is an "obvious" role: the real backend's default_form maps it
	// automatically, tag and all - this is the shape that reaches an admin
	// who never touched the left panel at all.
	default_form: {
		tabs: [
			{
				id: 'generation',
				label: 'Generation',
				icon: null,
				items: [
					{
						kind: 'field',
						field_name: 'steps',
						field_type: 'slider',
						label: 'Steps',
						default: { __exact_int__: JUST_OVER_SAFE },
						config: { min: 1, max: 150, step: 1 },
						mappings: [{ node_id: '3', input_name: 'steps', transform: 'none' }]
					}
				]
			}
		]
	},
	default_history: []
};

const IMPORT_RESULT = { preset_id: 'PRESET123', path: 'content/presets/local/SDXL/imported', mode: 'text2img', lint: { errors: [], warnings: [] } };

// Boundary control: exactly Number.MAX_SAFE_INTEGER (2**53-1) is the last
// integer a JSON number survives a browser's own JSON.parse exactly - the
// real backend's `_make_json_safe` does NOT tag it (see the parametrized
// `test_safe_integers_pass_through_unchanged` case for this exact value in
// `test_import_large_integer_literals.py`), so this fixture fakes the
// response the same way: a plain untagged number, never `__exact_int__`.
const MAX_SAFE = '9007199254740991';
const RAW_WORKFLOW_TEXT_BOUNDARY =
	'{"3":{"class_type":"KSampler","inputs":{"seed":123,"steps":' +
	MAX_SAFE +
	',"cfg":3.5,"sampler_name":"euler","scheduler":"normal","denoise":1.0}},' +
	'"4":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"sdxlBase_v10.safetensors"}},' +
	'"5":{"class_type":"EmptyLatentImage","inputs":{"width":832,"height":1216,"batch_size":1}},' +
	'"6":{"class_type":"CLIPTextEncode","inputs":{"text":"a cat","clip":["4",1]}},' +
	'"7":{"class_type":"CLIPTextEncode","inputs":{"text":"bad","clip":["4",1]}},' +
	'"8":{"class_type":"VAEDecode","inputs":{"samples":["3",0],"vae":["4",2]}},' +
	'"9":{"class_type":"SaveImage","inputs":{"images":["8",0],"filename_prefix":"ComfyUI"}}}';

const ANALYZE_RESULT_BOUNDARY = {
	mode: 'text2img',
	node_count: 7,
	sampler_node_id: '3',
	lora_chain: null,
	format: 'api',
	object_info_used: false,
	schema_fingerprint: 'fp-boundary',
	candidates: [
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'seed', current_value: 123, value_type: 'int', suggested_field_type: 'seed', suggested_field_name: 'seed', suggested_label: 'Seed', suggested_config: {}, role: 'seed' },
		{ node_id: '3', class_type: 'KSampler', node_title: 'KSampler', input_name: 'steps', current_value: 9007199254740991, value_type: 'int', suggested_field_type: 'slider', suggested_field_name: 'steps', suggested_label: 'Steps', suggested_config: { min: 1, max: 150, step: 1 }, role: 'steps' }
	],
	// Deliberately NOT pre-mapped in default_form - this exercises the
	// "Add to form" path (addCandidateToForm), not hydrateItem's tag
	// handling, since there is no tag here to handle.
	default_form: { tabs: [{ id: 'generation', label: 'Generation', icon: null, items: [] }] },
	default_history: []
};

async function loadDist(): Promise<any> {
	mkdirSync(STAGE_DIR, { recursive: true });
	const staged = resolve(STAGE_DIR, `import-workflow-tab-bigint-${Math.random().toString(36).slice(2)}.mjs`);
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

// The real POST /presets/import refuses a form entry with no mappings:
// `parse_form` -> `validate_against_workflow` raises "field '<name>': has no
// mappings" (backend/preset_import/schema.py), which api.py turns into a
// 400. Only a `lora_picker` is exempt (GRAPH_WIRED_FIELD_TYPES - the emitter
// wires that one into the graph itself). The mock below applies the same
// rule, so this fixture can no longer accept a payload the server rejects.
const GRAPH_WIRED_FIELD_TYPES = new Set(['lora_picker']);

function fieldsWithoutMappings(items: any[], acc: string[] = []): string[] {
	for (const it of items || []) {
		if (it.kind === 'field') {
			if ((it.mappings || []).length === 0 && !GRAPH_WIRED_FIELD_TYPES.has(it.field_type)) acc.push(it.field_name);
		} else if (it.items) {
			fieldsWithoutMappings(it.items, acc);
		}
	}
	return acc;
}

function importResponse(body: any) {
	const offenders = (body.form?.tabs || []).flatMap((t: any) => fieldsWithoutMappings(t.items));
	if (offenders.length > 0) {
		return jsonResponse({ detail: `Invalid form/history payload: field '${offenders[0]}': has no mappings` }, false);
	}
	return jsonResponse(IMPORT_RESULT);
}

function findPostedField(form: any, fieldName: string): any {
	const walk = (items: any[]): any => {
		for (const it of items || []) {
			if (it.kind === 'field' && it.field_name === fieldName) return it;
			if (it.items) {
				const found = walk(it.items);
				if (found) return found;
			}
		}
		return null;
	};
	for (const tab of form?.tabs || []) {
		const found = walk(tab.items);
		if (found) return found;
	}
	return null;
}

// Same controllable FileReader stand-in as importWorkflowSourceLifetime.test.ts -
// readAsText does nothing on its own, letting the test control exactly what
// text the reader "read" (here, the raw workflow text, so this test proves
// the file path never goes through this app's own JSON.parse/JSON.stringify
// either, same as the paste path).
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
	sessionStorage.clear();
	FakeFileReader.instances = [];
});

describe('ImportWorkflowTab large integer literals (real compiled dist)', () => {
	it('sends the exact pasted text as workflow_text to analyze/requirements/import, and imports with the tagged literal left out of the form entirely', async () => {
		const requests: Record<string, any> = {};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				requests.analyze = JSON.parse(String(init?.body));
				return jsonResponse(ANALYZE_RESULT);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') {
				requests.requirements = JSON.parse(String(init?.body));
				return jsonResponse({ results: [] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				requests.import = JSON.parse(String(init?.body));
				return importResponse(requests.import);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend', plugin: { id: 'comfyui-backend' } } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = RAW_WORKFLOW_TEXT;
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		// The analyze request carries the byte-exact pasted text, not a
		// re-derived JSON.stringify of whatever this app's own JSON.parse
		// made of it (which would already have rounded both literals).
		expect(requests.analyze.workflow_text).toBe(RAW_WORKFLOW_TEXT);

		// The seed candidate (foundational - always locked) never reaches
		// the DOM as raw text at all; the mapped, non-foundational "steps"
		// literal must render as its exact digits, never "[object Object]".
		const stepsRow = el.querySelector('[data-input-key="3:steps"]')!;
		expect(stepsRow.textContent).toContain('exact literal');
		expect(stepsRow.textContent).toContain(JUST_OVER_SAFE);
		expect(stepsRow.textContent).not.toContain('[object Object]');
		expect(stepsRow.querySelector('[data-action="add-input"]')).toBeNull();

		// "steps" arrived already mapped via default_form (an "obvious"
		// role): hydration drops it from the form outright, so there is no
		// card for it to garble and nothing to post. The locked left-panel
		// row above is the whole explanation the admin gets.
		expect(el.querySelector('[data-field-name="steps"]')).toBeNull();

		// The cfg candidate is ordinary and still addable normally.
		el.querySelector<HTMLButtonElement>('[data-input-key="3:cfg"] [data-action="add-input"]')!.click();
		await settle();
		expect(el.querySelector('[data-input-key="3:cfg"]')?.className).toContain('mapped');

		const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
		familyInput.value = 'SDXL';
		familyInput.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
		nameInput.value = 'Big int test';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();

		// Requirements preview also carries the exact text.
		expect(requests.requirements.workflow_text).toBe(RAW_WORKFLOW_TEXT);

		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();

		// Import carries the exact text too, and the posted form carries no
		// "steps" entry at all - a mapping-less entry is what the server
		// refuses, so the import has to succeed without one.
		expect(el.querySelector('[data-import-create-error]')?.textContent ?? null).toBeNull();
		expect(el.querySelector('[data-import-open-preset]')).toBeTruthy();
		expect(requests.import.workflow_text).toBe(RAW_WORKFLOW_TEXT);
		expect(findPostedField(requests.import.form, 'steps')).toBeNull();
		expect(requests.import.form.tabs.flatMap((t: any) => fieldsWithoutMappings(t.items))).toEqual([]);
		expect(findPostedField(requests.import.form, 'cfg').mappings).toEqual([
			{ node_id: '3', input_name: 'cfg', transform: 'none' }
		]);

		unmount(instance);
	});

	it('resubmits the /source-provided workflow_text verbatim on Update, not a re-serialized rounded workflow', async () => {
		sessionStorage.setItem('comfyui-import-edit-preset-id', 'EXISTING-ID');

		const SOURCE_RESPONSE = {
			workflow: JSON.parse(RAW_WORKFLOW_TEXT.replace(new RegExp(UINT64_MAX, 'g'), '{"__exact_int__":"' + UINT64_MAX + '"}').replace(new RegExp(JUST_OVER_SAFE, 'g'), '{"__exact_int__":"' + JUST_OVER_SAFE + '"}')),
			workflow_text: RAW_WORKFLOW_TEXT,
			mode: 'text2img',
			node_count: 7,
			sampler_node_id: '3',
			lora_chain: null,
			format: 'api',
			object_info_used: false,
			schema_fingerprint: 'fp-1',
			model_family: 'SDXL',
			variant: 'imported',
			display_name: 'Existing big-int preset',
			candidates: ANALYZE_RESULT.candidates,
			form: ANALYZE_RESULT.default_form,
			history: []
		};

		const requests: Record<string, any> = {};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/imported/EXISTING-ID/source') return jsonResponse(SOURCE_RESPONSE);
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') return jsonResponse({ results: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				requests.import = JSON.parse(String(init?.body));
				return importResponse(requests.import);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		// The stored "steps" field default is tagged too (a pre-existing
		// preset's own sidecar can carry one just as readily as a fresh
		// default_form) - hydration on the reload path drops it the same
		// way, never showing "[object Object]" or a rounded number.
		expect(el.querySelector('[data-field-name="steps"]')).toBeNull();
		expect(el.querySelector('[data-input-key="3:steps"]')!.textContent).toContain(JUST_OVER_SAFE);

		el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();

		// Resubmitted on Update as the literal stored text - never
		// JSON.stringify(SOURCE_RESPONSE.workflow), which would still carry
		// the __exact_int__ tag objects as real JSON, not the original ints.
		expect(requests.import.workflow_text).toBe(RAW_WORKFLOW_TEXT);
		expect(requests.import.workflow_text).not.toContain('__exact_int__');
		expect(requests.import.overwrite_preset_id).toBe('EXISTING-ID');
		expect(el.querySelector('[data-import-create-error]')?.textContent ?? null).toBeNull();
		expect(el.querySelector('[data-import-open-preset]')).toBeTruthy();
		expect(requests.import.form.tabs.flatMap((t: any) => fieldsWithoutMappings(t.items))).toEqual([]);

		unmount(instance);
	});

	it('sends the exact FILE text as workflow_text and applies the same tagged-literal behaviour on a file-based import', async () => {
		const requests: Record<string, any> = {};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				requests.analyze = JSON.parse(String(init?.body));
				return jsonResponse(ANALYZE_RESULT);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);
		vi.stubGlobal('FileReader', FakeFileReader);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const fileInput = el.querySelector<HTMLInputElement>('input[type="file"]')!;
		selectFile(fileInput, 'workflow.json');
		await settle();
		expect(FakeFileReader.instances).toHaveLength(1);
		FakeFileReader.instances[0].complete(RAW_WORKFLOW_TEXT);
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		// The analyze request carries the file's own exact text - never a
		// value derived from this app's own JSON.parse of it.
		expect(requests.analyze.workflow_text).toBe(RAW_WORKFLOW_TEXT);

		// Same tagged-literal handling as the paste path: "steps" (mapped
		// automatically via default_form, per ANALYZE_RESULT) is left out
		// of the form and shown only as a locked left-panel row.
		const stepsRow = el.querySelector('[data-input-key="3:steps"]')!;
		expect(stepsRow.textContent).toContain('exact literal');
		expect(stepsRow.textContent).toContain(JUST_OVER_SAFE);
		expect(stepsRow.querySelector('[data-action="add-input"]')).toBeNull();
		expect(el.querySelector('[data-field-name="steps"]')).toBeNull();

		unmount(instance);
	});

	it('does not tag or lock a literal exactly at Number.MAX_SAFE_INTEGER - it stays a normal editable field', async () => {
		const requests: Record<string, any> = {};
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/fields/types') return jsonResponse({ success: true, data: [] });
			if (url === '/api/plugins/comfyui-backend/presets/families') return jsonResponse({ families: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import/analyze') {
				requests.analyze = JSON.parse(String(init?.body));
				return jsonResponse(ANALYZE_RESULT_BOUNDARY);
			}
			if (url === '/api/plugins/comfyui-backend/presets/import/requirements') return jsonResponse({ results: [] });
			if (url === '/api/plugins/comfyui-backend/presets/import') {
				requests.import = JSON.parse(String(init?.body));
				return importResponse(requests.import);
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportWorkflowTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const textarea = el.querySelector<HTMLTextAreaElement>('textarea[data-import-json-input]')!;
		textarea.value = RAW_WORKFLOW_TEXT_BOUNDARY;
		textarea.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-analyze]')!.click();
		await settle();

		// At exactly MAX_SAFE_INTEGER, the row is a plain unmapped candidate -
		// never treated as locked/exact, and offered a normal Add button.
		const stepsRow = el.querySelector('[data-input-key="3:steps"]')!;
		expect(stepsRow.className).not.toContain('locked');
		expect(stepsRow.textContent).not.toContain('exact literal');
		expect(stepsRow.textContent).toContain(MAX_SAFE);
		const addBtn = stepsRow.querySelector<HTMLButtonElement>('[data-action="add-input"]');
		expect(addBtn).toBeTruthy();
		addBtn!.click();
		await settle();

		// Adding it produces an ordinary editable field showing the exact
		// value, not read-only.
		const stepsCard = el.querySelector('[data-field-name="steps"]')!;
		expect(stepsCard).toBeTruthy();
		const defaultInput = stepsCard.querySelector<HTMLInputElement>('.di-field-default')!;
		expect(defaultInput.value).toBe(MAX_SAFE);
		expect(defaultInput.readOnly).toBe(false);
		expect(stepsCard.querySelector('.di-mapping')?.textContent).toContain('3.inputs.steps');

		const familyInput = el.querySelector<HTMLInputElement>('#import-model-family')!;
		familyInput.value = 'SDXL';
		familyInput.dispatchEvent(new Event('input', { bubbles: true }));
		const nameInput = el.querySelector<HTMLInputElement>('#import-display-name')!;
		nameInput.value = 'Boundary test';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		el.querySelector<HTMLButtonElement>('button[data-import-continue-form]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-continue-history]')!.click();
		await settle();
		el.querySelector<HTMLButtonElement>('button[data-import-create]')!.click();
		await settle();

		// The submitted form carries it as a plain JSON number, exact at
		// this boundary - never a string, never a tag object.
		const stepsField = requests.import.form.tabs[0].items.find((it: any) => it.field_name === 'steps');
		expect(stepsField.default).toBe(9007199254740991);
		expect(typeof stepsField.default).toBe('number');
		expect(stepsField.mappings).toEqual([{ node_id: '3', input_name: 'steps', transform: 'none' }]);

		unmount(instance);
	});
});
