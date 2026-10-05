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

const MARKER = 'FIRSTTABFORMMARKER';
const SCHEMA = { properties: { marker: { type: 'string', name: 'marker', title: MARKER } } };

vi.mock('$lib/services/api', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	const api = new Proxy(
		{},
		{
			get: (_target, key: string) => async () => {
				if (key === 'getPresetFormSchema') return { success: true, data: { form_schema: SCHEMA } };
				if (key === 'getPresetModes') {
					return { success: true, data: { modes: [{ name: 'image', label: 'Image', variants: [] }], default_mode: 'image' } };
				}
				if (key === 'getPreset') return { success: true, data: { vars: {}, styles: [] } };
				if (key === 'listPresets') {
					return { success: true, data: [{ id: 'p1', name: 'Preset One', version: '1', engine: 'native', modes: ['image'] }] };
				}
				if (key === 'getReadiness') return { checks: [], ready: true, status: 'ready' };
				return { success: false, error: 'unused', data: null };
			}
		}
	);
	return { ...real, api };
});

Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true });

const { mount, unmount, flushSync } = await import('svelte');
const { get } = await import('svelte/store');
const { tabsStore } = await import('$lib/stores/tabs');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { default: Page } = await import('../../src/routes/generate/+page.svelte');

registerBuiltinFieldComponents();

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(check: () => boolean, label: string) {
	for (let i = 0; i < 100; i++) {
		if (check()) return;
		await wait(10);
	}
	throw new Error(`timed out waiting for ${label}`);
}

let teardown: (() => void) | undefined;

afterEach(() => {
	teardown?.();
	teardown = undefined;
});

describe('Generate page tabs never show another tab form', () => {
	it('a new tab renders its own empty state in the same flush and the first tab form is gone', async () => {
		const firstId = get(tabsStore).tabs[0].id;
		tabsStore.updateTab(firstId, { selectedPreset: 'p1', selectedMode: 'image' });
		const target = document.body.appendChild(document.createElement('div'));
		const app = mount(Page as never, { target, props: {} });
		teardown = () => {
			unmount(app);
			target.remove();
		};
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the first tab form');

		const seen: boolean[] = [];
		const observer = new MutationObserver(() => seen.push(target.textContent?.includes(MARKER) ?? false));
		observer.observe(target, { childList: true, subtree: true, characterData: true });

		tabsStore.addTab();
		flushSync();

		expect(get(tabsStore).activeTabId).not.toBe(firstId);
		expect(target.textContent).not.toContain(MARKER);
		expect(target.textContent).toContain('Ready to generate');

		await wait(100);
		observer.disconnect();
		expect(seen.every((hasMarker) => !hasMarker)).toBe(true);
		expect(target.textContent).not.toContain(MARKER);
	});

	it('switching back to the first tab shows its form again', async () => {
		const firstId = get(tabsStore).tabs[0].id;
		const target = document.body.appendChild(document.createElement('div'));
		const app = mount(Page as never, { target, props: {} });
		teardown = () => {
			unmount(app);
			target.remove();
		};
		if (get(tabsStore).tabs.length < 2) tabsStore.addTab();
		else tabsStore.setActiveTab(get(tabsStore).tabs[1].id);
		flushSync();
		expect(target.textContent).not.toContain(MARKER);

		tabsStore.setActiveTab(firstId);
		flushSync();
		await until(() => target.textContent?.includes(MARKER) ?? false, 'the first tab form again');
	});
});
