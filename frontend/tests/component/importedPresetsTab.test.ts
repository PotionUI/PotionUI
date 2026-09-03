// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist for the "Imported
// presets" admin tab (see manifest.yml admin_tabs, backend/api.py's GET
// /presets/imported) - same stage/load approach as importWorkflowTab.test.ts.
import { describe, expect, it, vi, beforeAll, afterEach } from 'vitest';
import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mount, unmount, flushSync } from 'svelte';
import { _wrapPluginDistComponent } from '$lib/plugin-api/componentResolver';

const REPO_ROOT = resolve(__dirname, '../../..');
const DIST_PATH = 'content/plugins/marketplace/comfyui-backend/frontend/dist/ImportedPresetsTab.js';
const STAGE_DIR = resolve(__dirname, '../../node_modules/.plugin-dist-under-test');

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

let ImportedPresetsTab: any;

beforeAll(async () => {
	if (!existsSync(resolve(REPO_ROOT, DIST_PATH))) {
		throw new Error(`${DIST_PATH} is missing - run \`node scripts/build-plugins.mjs comfyui-backend\` first.`);
	}
	const raw = await loadDist();
	ImportedPresetsTab = _wrapPluginDistComponent(raw);
});

afterEach(() => {
	vi.unstubAllGlobals();
	document.body.innerHTML = '';
});

describe('ImportedPresetsTab (real compiled dist)', () => {
	it('renders a row per imported preset with format/requirements chips and an Open in Presets link', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') {
				return jsonResponse({
					presets: [
						{
							preset_id: 'P1',
							name: 'LTX 2.5 — My workflow',
							family: 'LTX',
							variant: 'imported',
							format: 'api',
							requirements_summary: { ok: 6, missing: 2, unknown: 0, optional_missing: 0 },
							created_at: Math.floor(Date.now() / 1000) - 120
						},
						{
							preset_id: 'P2',
							name: 'Never checked',
							family: 'SDXL',
							variant: 'imported',
							format: 'ui',
							requirements_summary: null,
							created_at: Math.floor(Date.now() / 1000) - 3600
						}
					]
				});
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const table = el.querySelector('[data-imported-presets]');
		expect(table).toBeTruthy();
		expect(table!.textContent).toContain('LTX 2.5 — My workflow');
		expect(table!.textContent).toContain('LTX · imported');

		const rows = el.querySelectorAll('.ip-row:not(.ip-head)');
		expect(rows).toHaveLength(2);

		const firstRow = rows[0];
		expect(firstRow.textContent).toContain('6/8');
		expect(firstRow.querySelector('a')?.getAttribute('href')).toBe('/admin?tab=presets&preset=P1');

		// Never-checked requirements_summary shows the "—" fallback, not a chip claiming a count.
		expect(rows[1].textContent).toContain('—');

		unmount(instance);
	});

	it('shows an empty state when nothing has been imported yet', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') return jsonResponse({ presets: [] });
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		expect(el.querySelector('[data-imported-presets]')).toBeNull();
		expect(el.textContent).toContain('No presets have been imported yet');

		unmount(instance);
	});
});
