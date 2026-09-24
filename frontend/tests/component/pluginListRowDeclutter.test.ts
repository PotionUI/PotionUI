// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';

type PageStore = Writable<{ url: URL }>;

vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		invalidate: async () => {},
		invalidateAll: async () => {},
		preloadData: async () => {},
		preloadCode: async () => {},
		afterNavigate: () => {},
		beforeNavigate: () => {},
		pushState: () => {},
		replaceState: () => {}
	};
});

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const SHORT_PLUGIN = {
	id: 'short-plugin',
	name: 'Short Plugin',
	version: '1.0.0',
	type: 'backend',
	enabled: true,
	manifest_path: '/plugins/short-plugin/manifest.yml',
	description: 'A brief description.',
	category: 'workflow',
	tags: ['alpha'],
	capabilities: ['does-a-thing'],
	source: 'marketplace',
	hook_count: 2,
	settings_count: 1
};

const LONG_PLUGIN = {
	id: 'long-plugin',
	name: 'Long Plugin',
	version: '2.3.1',
	type: 'full-stack',
	enabled: false,
	manifest_path: '/plugins/local/long-plugin/manifest.yml',
	description:
		'This description is deliberately long so that it would wrap onto more than one line once rendered inside the narrow list pane, which is exactly the case the expand chevron needs to catch.',
	category: 'workflow',
	tags: [],
	capabilities: ['streams-video', 'transcodes-audio'],
	source: 'local',
	shadows: 'content/plugins/marketplace/long-plugin',
	hook_count: 0,
	settings_count: 0
};

const apiState: { plugins: (typeof SHORT_PLUGIN | typeof LONG_PLUGIN)[] } = {
	plugins: [SHORT_PLUGIN, LONG_PLUGIN]
};

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			getClient: () => ({
				get: vi.fn(async (url: string) => {
					if (url === '/api/plugins') return { data: { success: true, data: apiState.plugins } };
					if (url === '/api/plugins/hooks/frontend') return { data: { success: true, data: {} } };
					if (url === '/api/plugins/frontend-extensions') {
						return { data: { success: true, data: { renderers: [], contributions: [], revisions: {} } } };
					}
					if (url === '/api/fields/types') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/pages') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/quick-actions') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/sidebar-widgets') return { data: { success: true, data: [] } };
					const match = apiState.plugins.find((p) => url === `/api/plugins/${p.id}`);
					if (match) return { data: { success: true, data: { ...match, settings_schema: [], settings_values: {}, hooks: [] } } };
					throw new Error(`unexpected GET ${url}`);
				}),
				post: vi.fn(async () => ({ data: { success: true } }))
			})
		}
	};
});

const { default: PluginsTab } = await import('../../src/routes/admin/components/PluginsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { libraryCardDensity } = await import('$lib/components/library/libraryCardDensity');

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PluginsTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function listPane(target: HTMLElement): HTMLElement {
	const pane = target.querySelector('[role="list"][aria-label="Plugin catalog"]') as HTMLElement | null;
	expect(pane, 'expected a card grid for the plugin list').toBeTruthy();
	return pane!;
}

function clickPluginRow(target: HTMLElement, name: string) {
	const row = Array.from(target.querySelectorAll('[data-library-card]')).find((el) => el.textContent?.includes(name)) as
		| HTMLElement
		| undefined;
	expect(row, `expected a card for ${name}`).toBeTruthy();
	flushSync(() => row!.click());
}

let mounted: ReturnType<typeof mount> | undefined;
let originalRO: unknown;

beforeEach(() => {
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = StubResizeObserver;
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	apiState.plugins = [SHORT_PLUGIN, LONG_PLUGIN];
	(globalThis as any).ResizeObserver = originalRO;
	page.update((current) => ({ ...current, url: new URL('http://localhost/admin') }));
	libraryCardDensity.set('compact');
});

describe('PluginsTab list row declutter', () => {
	it('keeps source, count and shadow chips out of the list row', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);

		expect(pane.textContent).toContain('Short Plugin');
		expect(pane.textContent).toContain('v1.0.0');
		expect(pane.textContent).toContain('A brief description.');

		expect(pane.textContent).not.toContain('MARKETPLACE');
		expect(pane.textContent).not.toContain('HOOKS');
		expect(pane.textContent).not.toContain('SETTINGS');
		expect(pane.textContent).not.toContain('SHADOWS MARKETPLACE COPY');
	});

	it('renders a disabled plugin name muted, with no "Disabled" chip in the list', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		const nameEl = Array.from(pane.querySelectorAll('span')).find((el) => el.textContent === 'Long Plugin') as
			| HTMLElement
			| undefined;
		expect(nameEl).toBeTruthy();
		expect(nameEl!.className).toContain('text-fg-muted');
		expect(pane.textContent).not.toContain('Disabled');
	});

	it('shows the shadow warning in the detail header, not the list', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		expect(pane.textContent).not.toContain('SHADOWS MARKETPLACE COPY');

		clickPluginRow(mounted.target, 'Long Plugin');
		await settle();

		expect(mounted.target.textContent).toContain('SHADOWS MARKETPLACE COPY');
	});

	it('clamps the row description to two lines', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		const description = Array.from(pane.querySelectorAll('p')).find((el) =>
			el.textContent?.includes('deliberately long')
		) as HTMLElement;
		expect(description).toBeTruthy();
		expect(description.className).toContain('line-clamp-2');
	});

	it('the density toggle switches the grid to dense cards and hides descriptions', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		expect(pane.className).toContain('minmax(300px');
		expect(pane.textContent).toContain('A brief description.');

		const denseButton = Array.from(mounted.target.querySelectorAll('button[role="radio"]')).find(
			(el) => el.textContent?.trim() === 'Dense'
		) as HTMLButtonElement | undefined;
		expect(denseButton, 'expected a Dense option in the density toggle').toBeTruthy();
		flushSync(() => denseButton!.click());
		await settle();

		const densePane = listPane(mounted.target);
		expect(densePane.className).toContain('minmax(240px');
		expect(densePane.textContent).not.toContain('A brief description.');
	});

	it('clicking the enable switch on a card toggles the plugin without opening its detail view', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		const card = Array.from(pane.querySelectorAll('[data-library-card]')).find((el) =>
			el.textContent?.includes('Short Plugin')
		) as HTMLElement | undefined;
		expect(card, 'expected a card for Short Plugin').toBeTruthy();

		const switchInput = card!.querySelector('input[type="checkbox"][role="switch"]') as HTMLInputElement | null;
		expect(switchInput, 'expected the card to carry the enable switch').toBeTruthy();
		expect(switchInput!.checked).toBe(true);

		flushSync(() => switchInput!.click());
		await settle();

		expect(mounted.target.querySelector('[role="list"][aria-label="Plugin catalog"]')).toBeTruthy();
		expect(mounted.target.querySelector('nav[aria-label="Plugin details"]')).toBeFalsy();
	});
});
