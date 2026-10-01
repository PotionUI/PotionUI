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

const PLUGINS = [
	{
		id: 'comfyui-backend',
		name: 'ComfyUI Backend',
		version: '1.0.0',
		type: 'full-stack',
		enabled: true,
		manifest_path: '/plugins/comfyui-backend/manifest.yml',
		description: 'Runs generations on a ComfyUI server.',
		category: 'backends',
		tags: [],
		capabilities: ['workflow-import-slug'],
		source: 'marketplace'
	},
	{
		id: 'civitai-provider',
		name: 'CivitAI Provider',
		version: '2.0.0',
		type: 'backend-only',
		enabled: true,
		manifest_path: '/plugins/civitai-provider/manifest.yml',
		description: 'Browse CivitAI.',
		category: 'sources',
		tags: [],
		capabilities: [],
		source: 'marketplace'
	},
	{
		id: 'legacy-plugin',
		name: 'Legacy Plugin',
		version: '0.1.0',
		type: 'frontend-only',
		enabled: true,
		manifest_path: '/plugins/legacy-plugin/manifest.yml',
		description: 'Has an old category value.',
		category: 'workflow',
		tags: [],
		capabilities: [],
		source: 'marketplace'
	}
];

const apiState: { plugins: typeof PLUGINS; hooks: Record<string, unknown>[] } = {
	plugins: PLUGINS,
	hooks: [
		{
			id: 1,
			plugin_id: 'comfyui-backend',
			hook_name: 'generation.before-run-hook',
			hook_type: 'backend',
			handler_path: 'backend.hooks.run',
			sort_order: 10
		}
	]
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
					if (match) return { data: { success: true, data: { ...match, settings_schema: [], settings_values: {}, hooks: apiState.hooks } } };
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
	(globalThis as any).ResizeObserver = originalRO;
	page.update((current) => ({ ...current, url: new URL('http://localhost/admin') }));
});

function technicalToggle(target: HTMLElement): HTMLButtonElement {
	const section = Array.from(target.querySelectorAll('section')).find((el) =>
		el.textContent?.includes('Technical details')
	) as HTMLElement | undefined;
	expect(section, 'expected a Technical details section').toBeTruthy();
	return section!.querySelector('button[aria-expanded]') as HTMLButtonElement;
}

describe('PluginsTab categories and technical details', () => {
	it('lists the card with its category badge and without the build-type badge', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		expect(pane.textContent).toContain('ComfyUI Backend');
		expect(pane.textContent).toContain('Backends & compute');
		expect(pane.textContent).toContain('Model sources');
		expect(pane.textContent).not.toMatch(/full-stack|backend-only|frontend-only/i);
	});

	it('shows a legacy category as Other', async () => {
		mounted = mount();
		await settle();

		const card = Array.from(listPane(mounted.target).querySelectorAll('[data-library-card]')).find((el) =>
			el.textContent?.includes('Legacy Plugin')
		) as HTMLElement;
		expect(card.textContent).toContain('Other');
	});

	it('names the sidebar sections after what a plugin adds', async () => {
		mounted = mount();
		await settle();

		const text = mounted.target.textContent ?? '';
		const labels = [
			'Backends & compute',
			'Model sources',
			'Generation steps',
			'Tools & pages',
			'Sign-in & security',
			'Monitoring',
			'Developer',
			'Other'
		];
		let cursor = -1;
		for (const label of labels) {
			const at = text.indexOf(label, cursor + 1);
			expect(at, `expected section ${label} after the previous one`).toBeGreaterThan(cursor);
			cursor = at;
		}
		expect(text).not.toContain('Generation & Backends');
	});

	it('offers no Type filter', async () => {
		mounted = mount();
		await settle();

		const filterButton = Array.from(mounted.target.querySelectorAll('button')).find((el) =>
			/filters?/i.test(el.getAttribute('aria-label') ?? el.textContent ?? '')
		) as HTMLButtonElement | undefined;
		if (filterButton) {
			flushSync(() => filterButton.click());
			await settle();
		}
		expect(document.body.textContent).not.toContain('All types');
	});

	it('keeps the type, capabilities and hooks out of the header and behind a collapsed Technical details section', async () => {
		mounted = mount();
		await settle();

		const row = Array.from(mounted.target.querySelectorAll('[data-library-card]')).find((el) =>
			el.textContent?.includes('ComfyUI Backend')
		) as HTMLElement;
		flushSync(() => row.click());
		await settle();

		const text = () => mounted!.target.textContent ?? '';
		expect(text()).not.toMatch(/FULL-STACK/);
		expect(text()).toContain('v1.0.0');
		expect(text()).toContain('Technical details');
		expect(text()).not.toContain('workflow-import-slug');
		expect(text()).not.toContain('generation.before-run-hook');

		const toggle = technicalToggle(mounted.target);
		expect(toggle.getAttribute('aria-expanded')).toBe('false');
		flushSync(() => toggle.click());
		await settle();

		expect(technicalToggle(mounted.target).getAttribute('aria-expanded')).toBe('true');
		expect(text()).toContain('Server and interface parts');
		expect(text()).toContain('workflow-import-slug');
		expect(text()).toContain('generation.before-run-hook');
	});
});
