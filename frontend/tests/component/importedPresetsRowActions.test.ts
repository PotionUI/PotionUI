// @vitest-environment jsdom
//
// Drives the REAL compiled comfyui-backend plugin dist for the "Imported
// presets" row actions: reload (spinner while in flight, then an inline
// lint-result chip), edit (writes the cross-tab sessionStorage handoff and
// dispatches the generic potionui:switch-plugin-tab event the host listens
// for), and delete (hand-built confirm modal, Esc/Enter, row disappears on
// confirm). Same stage/load approach as importedPresetsTab.test.ts.
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

const PRESET_ROW = {
	preset_id: 'P1',
	name: 'LTX 2.5 — My workflow',
	family: 'LTX',
	variant: 'imported',
	format: 'api',
	has_sidecar: true,
	requirements_summary: { ok: 6, missing: 0, unknown: 0, optional_missing: 0 },
	created_at: Math.floor(Date.now() / 1000) - 120
};

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
	sessionStorage.clear();
});

describe('ImportedPresetsTab row actions (real compiled dist)', () => {
	it('reload shows a spinner then an inline lint-result chip, and refreshes the list', async () => {
		let reloadCalled = false;
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') {
				return jsonResponse({ presets: [reloadCalled ? { ...PRESET_ROW, name: 'Reloaded name' } : PRESET_ROW] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/imported/P1/reload' && init?.method === 'POST') {
				reloadCalled = true;
				return jsonResponse({ preset_id: 'P1', path: 'content/presets/local/LTX/imported', mode: 'txt2img', lint: { errors: [], warnings: [] } });
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		const reloadBtn = el.querySelector<HTMLButtonElement>('button[aria-label="Reload from source"]')!;
		expect(reloadBtn).toBeTruthy();
		reloadBtn.click();
		await settle();

		const resultLine = el.querySelector('.ip-reload-result');
		expect(resultLine?.textContent).toContain('lint clean');
		// The row's own data refreshed after reload.
		expect(el.textContent).toContain('Reloaded name');

		unmount(instance);
	});

	it('reload shows the danger tone on a lint error', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') return jsonResponse({ presets: [PRESET_ROW] });
			if (url === '/api/plugins/comfyui-backend/presets/imported/P1/reload') {
				return jsonResponse({ preset_id: 'P1', path: 'x', mode: 'txt2img', lint: { errors: ['bad requirement'], warnings: [] } });
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		el.querySelector<HTMLButtonElement>('button[aria-label="Reload from source"]')!.click();
		await settle();

		const resultLine = el.querySelector('.ip-reload-result');
		expect(resultLine?.className).toContain('ip-reload-danger');
		expect(resultLine?.textContent).toContain('1 lint error');

		unmount(instance);
	});

	it('edit stashes the preset id in sessionStorage and asks the host to switch tabs', async () => {
		const fetchMock = vi.fn(async (url: string) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') return jsonResponse({ presets: [PRESET_ROW] });
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const switchHandler = vi.fn();
		window.addEventListener('potionui:switch-plugin-tab', switchHandler);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		el.querySelector<HTMLButtonElement>('button[aria-label="Edit"]')!.click();
		await settle();

		expect(sessionStorage.getItem('comfyui-import-edit-preset-id')).toBe('P1');
		expect(switchHandler).toHaveBeenCalledTimes(1);
		const event = switchHandler.mock.calls[0][0] as CustomEvent;
		expect(event.detail).toEqual({ pluginId: 'comfyui-backend', tabId: 'import-workflow' });

		window.removeEventListener('potionui:switch-plugin-tab', switchHandler);
		unmount(instance);
	});

	it('delete asks for confirmation, Escape cancels, Enter confirms and removes the row', async () => {
		let deleted = false;
		const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
			if (url === '/api/plugins/comfyui-backend/presets/imported') {
				return jsonResponse({ presets: deleted ? [] : [PRESET_ROW] });
			}
			if (url === '/api/plugins/comfyui-backend/presets/imported/P1' && init?.method === 'DELETE') {
				deleted = true;
				return jsonResponse({ deleted: true, preset_id: 'P1' });
			}
			throw new Error(`Unexpected fetch: ${url}`);
		});
		vi.stubGlobal('fetch', fetchMock);

		const el = target();
		const instance = mount(ImportedPresetsTab, { target: el, props: { pluginId: 'comfyui-backend' } });
		await settle();

		el.querySelector<HTMLButtonElement>('button[aria-label="Delete"]')!.click();
		await settle();

		let dialog = document.querySelector('[role="dialog"][aria-label="Delete imported preset"]');
		expect(dialog).toBeTruthy();
		expect(dialog!.textContent).toContain('LTX 2.5 — My workflow');

		// Escape cancels without deleting.
		flushSync(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
		await settle();
		expect(document.querySelector('[role="dialog"][aria-label="Delete imported preset"]')).toBeNull();
		expect(fetchMock.mock.calls.some((c) => c[1]?.method === 'DELETE')).toBe(false);

		// Re-open, this time confirm with Enter.
		el.querySelector<HTMLButtonElement>('button[aria-label="Delete"]')!.click();
		await settle();
		dialog = document.querySelector('[role="dialog"][aria-label="Delete imported preset"]');
		expect(dialog).toBeTruthy();

		flushSync(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
		await settle();

		expect(document.querySelector('[role="dialog"][aria-label="Delete imported preset"]')).toBeNull();
		expect(el.querySelector('[data-imported-presets]')).toBeNull();
		expect(el.textContent).toContain('No presets have been imported yet');

		unmount(instance);
	});
});
