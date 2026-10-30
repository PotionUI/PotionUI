import { describe, it, expect, vi, afterEach } from 'vitest';

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

vi.mock('$lib/services/api', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	const api = new Proxy(
		{},
		{
			get: (_target, key: string) => async () => {
				if (key === 'getPresetFormSchema') return { success: true, data: { form_schema: { properties: {} } } };
				if (key === 'getPresetModes') {
					return { success: true, data: { modes: [{ name: 'image', label: 'Picture mode', variants: [] }], default_mode: 'image' } };
				}
				if (key === 'getPreset') return { success: true, data: { vars: { promptless_modes: ['image'] }, styles: [] } };
				if (key === 'listPresets') {
					return {
						success: true,
						data: [{ id: 'flux1', name: 'FLUX.1 Dev', version: '1', engine: 'comfyui', modes: ['image'], engine_available: true }]
					};
				}
				if (key === 'getModeLabels') return { success: true, data: { labels: { image: 'Generic image' } } };
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
const { viewportWidth } = await import('$lib/stores/viewport');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { invalidatePresets } = await import('$lib/stores/presetsCatalog');
const { default: Page } = await import('../../src/routes/generate/+page.svelte');

registerBuiltinFieldComponents();

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let teardown: (() => void) | undefined;

afterEach(() => {
	teardown?.();
	teardown = undefined;
	viewportWidth.set(1280);
	const firstId = get(tabsStore).tabs[0].id;
	tabsStore.updateTab(firstId, { workbenchFloating: false });
});

async function render(width: number, floating: boolean) {
	viewportWidth.set(width);
	invalidatePresets();
	const firstId = get(tabsStore).tabs[0].id;
	tabsStore.updateTab(firstId, { selectedPreset: 'flux1', selectedMode: 'image', workbenchFloating: floating });
	const target = document.body.appendChild(document.createElement('div'));
	const app = mount(Page as never, { target, props: {} });
	teardown = () => {
		unmount(app);
		target.remove();
	};
	for (let i = 0; i < 100 && !target.querySelector('.generation-panel'); i++) await wait(10);
	await wait(50);
	return target;
}

const queueButton = (target: HTMLElement) => target.querySelector<HTMLButtonElement>('.generation-panel button[aria-label="Generation queue"]')!;

describe('generate page deck label', () => {
	it('names the mode the way the mode picker does, not by the generic name', async () => {
		const target = await render(1280, false);
		const panel = target.querySelector('.generation-panel')!;

		expect(panel.textContent).toContain('FLUX.1 Dev · Picture mode');
		expect(target.textContent).not.toContain('Generic image');
	});
});
