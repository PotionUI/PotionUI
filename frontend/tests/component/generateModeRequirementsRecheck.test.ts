import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

vi.mock('$lib/services/websocket', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return {
		...real,
		createGenerationSocket: () => ({
			onConnectionChange() {},
			connect() {},
			disconnect() {},
			on() {},
			off() {},
			send() {},
			subscribe() {}
		})
	};
});

vi.mock('$lib/utils/modeRequirementsRecheck', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/utils/modeRequirementsRecheck')>();
	return {
		...real,
		createModeRequirementsRecheck: (deps: Parameters<typeof real.createModeRequirementsRecheck>[0]) =>
			real.createModeRequirementsRecheck({ ...deps, retryMs: 40 })
	};
});

const LORA = 'ltx-2.5-22b-ic-lora-day-to-night-0.9.safetensors';

const server = vi.hoisted(() => ({
	missing: [] as string[],
	modesCalls: 0,
	formSchemaCalls: 0,
	failModes: false
}));

vi.mock('$lib/services/api', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	const variants = [
		{ name: 'standard', label: 'Standard', default: true, order: 0 },
		{ name: 'fast', label: 'Fast', default: false, order: 1 }
	];
	const api = new Proxy(
		{},
		{
			get: (_target, key: string) => async () => {
				if (key === 'getPresetFormSchema') {
					server.formSchemaCalls += 1;
					return { success: true, data: { form_schema: { properties: {} } } };
				}
				if (key === 'getPresetModes') {
					server.modesCalls += 1;
					if (server.failModes) throw new Error('offline');
					return {
						success: true,
						data: {
							modes: [
								{ name: 'day_to_night', label: 'Day to night', variants, requirements_missing: [...server.missing] },
								{ name: 'txt2vid', label: 'Text to video', variants, requirements_missing: [] }
							],
							default_mode: 'txt2vid'
						}
					};
				}
				if (key === 'getPreset') {
					return { success: true, data: { vars: { promptless_modes: ['day_to_night', 'txt2vid'] }, styles: [] } };
				}
				if (key === 'listPresets') {
					return {
						success: true,
						data: [
							{
								id: 'ltx25',
								name: 'LTX-2.5',
								version: '1',
								engine: 'native',
								modes: ['day_to_night', 'txt2vid'],
								engine_available: true
							}
						]
					};
				}
				if (key === 'getReadiness') return { checks: [], ready: true, status: 'ready' };
				return { success: false, error: 'unused', data: null };
			}
		}
	);
	return { ...real, api };
});

Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true });

const { mount, unmount } = await import('svelte');
const { get } = await import('svelte/store');
const { tabsStore } = await import('$lib/stores/tabs');
const { toasts } = await import('$lib/stores/toast');
const { announceModelInstalled } = await import('$lib/stores/modelInstalls');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { invalidatePresets } = await import('$lib/stores/presetsCatalog');
const { default: Page } = await import('../../src/routes/generate/+page.svelte');

registerBuiltinFieldComponents();

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(condition: () => boolean, limitMs = 1500) {
	for (let waited = 0; waited < limitMs && !condition(); waited += 10) await wait(10);
}

let teardown: (() => void) | undefined;

beforeEach(() => {
	server.missing = [LORA];
	server.modesCalls = 0;
	server.formSchemaCalls = 0;
	server.failModes = false;
	tabsStore.reset();
});

afterEach(() => {
	teardown?.();
	teardown = undefined;
	vi.restoreAllMocks();
});

const MESSAGE = 'A required file is missing.';

function generateButton(target: HTMLElement) {
	return target.querySelector('button.generate-button') as HTMLButtonElement;
}

function activeTab() {
	const state = get(tabsStore);
	return generationTabs(state.tabs).find((tab) => tab.id === state.activeTabId)!;
}

async function render() {
	invalidatePresets();
	tabsStore.updateTab(generationTabs(get(tabsStore).tabs)[0].id, {
		selectedPreset: 'ltx25',
		selectedMode: 'day_to_night',
		selectedVariant: 'fast',
		formData: { strength: 0.7, note: 'keep me' }
	});
	const target = document.body.appendChild(document.createElement('div'));
	const app = mount(Page as never, { target, props: {} });
	teardown = () => {
		unmount(app);
		target.remove();
	};
	await until(() => target.textContent?.includes('LTX-2.5') ?? false);
	await until(() => server.modesCalls > 0);
	await wait(60);
	return target;
}

describe('Generate re-checks a mode file requirement after a model is installed', () => {
	it('unblocks Generate without a reload and leaves mode, variant and form untouched', async () => {
		const target = await render();
		expect(generateButton(target).disabled).toBe(true);
		expect(target.textContent).toContain(MESSAGE);

		const before = activeTab();
		const formBefore = JSON.parse(JSON.stringify(before.formData));
		const schemaCallsBefore = server.formSchemaCalls;
		const modesCallsBefore = server.modesCalls;

		server.missing = [];
		announceModelInstalled();
		await until(() => !generateButton(target).disabled);

		expect(generateButton(target).disabled).toBe(false);
		expect(target.textContent).not.toContain(MESSAGE);
		expect(server.modesCalls).toBe(modesCallsBefore + 1);

		const after = activeTab();
		expect(after.id).toBe(before.id);
		expect(after.selectedPreset).toBe('ltx25');
		expect(after.selectedMode).toBe('day_to_night');
		expect(after.selectedVariant).toBe('fast');
		expect(after.formData).toEqual(formBefore);
		expect(server.formSchemaCalls).toBe(schemaCallsBefore);
	});

	it('checks again shortly after when the first answer still says the file is missing', async () => {
		const target = await render();
		const modesCallsBefore = server.modesCalls;

		announceModelInstalled();
		await until(() => server.modesCalls > modesCallsBefore);
		expect(generateButton(target).disabled).toBe(true);

		server.missing = [];
		await until(() => !generateButton(target).disabled);

		expect(generateButton(target).disabled).toBe(false);
		expect(server.modesCalls).toBe(modesCallsBefore + 2);
		await wait(150);
		expect(server.modesCalls).toBe(modesCallsBefore + 2);
		expect(activeTab().selectedMode).toBe('day_to_night');
		expect(activeTab().selectedVariant).toBe('fast');
	});

	it('does not ask the server again when the selected mode has nothing missing', async () => {
		server.missing = [];
		const target = await render();
		expect(generateButton(target).disabled).toBe(false);
		const modesCallsBefore = server.modesCalls;

		announceModelInstalled();
		await wait(150);

		expect(server.modesCalls).toBe(modesCallsBefore);
	});

	it('does not ask the server again for a tab that is not the active one', async () => {
		await render();
		const modesCallsBefore = server.modesCalls;

		tabsStore.addTab();
		await wait(60);
		const modesCallsAfterSwitch = server.modesCalls;
		announceModelInstalled();
		await wait(150);

		expect(server.modesCalls).toBe(modesCallsAfterSwitch);
		expect(modesCallsAfterSwitch).toBe(modesCallsBefore);
	});

	it('stays quiet when the background re-check cannot reach the server', async () => {
		const target = await render();
		const toastError = vi.spyOn(toasts, 'error');
		const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
		const modesCallsBefore = server.modesCalls;
		server.failModes = true;

		announceModelInstalled();
		await until(() => server.modesCalls > modesCallsBefore);
		await wait(150);

		expect(toastError).not.toHaveBeenCalled();
		expect(consoleError).toHaveBeenCalled();
		expect(generateButton(target).disabled).toBe(true);
		expect(activeTab().selectedMode).toBe('day_to_night');
		expect(activeTab().selectedVariant).toBe('fast');
	});

	it('stops listening once the page is gone', async () => {
		await render();
		teardown?.();
		teardown = undefined;
		const modesCallsBefore = server.modesCalls;

		announceModelInstalled();
		await wait(150);

		expect(server.modesCalls).toBe(modesCallsBefore);
	});
});
