// @vitest-environment jsdom
//
// The plugin list row must show only name, version, and a one-line-clamped
// description - the source/type/shadow/capability/hook-count/settings-count
// chips it used to carry move to the detail pane (or get dropped if the
// detail pane already shows the equivalent, richer information). This mounts
// the real PluginsTab list against fixtures carrying all of those fields and
// proves the list omits them while the detail header still surfaces the
// shadow warning.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';

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
	const pane = target.querySelector('[role="listbox"]') as HTMLElement | null;
	expect(pane, 'expected a listbox pane for the plugin list').toBeTruthy();
	return pane!;
}

function clickPluginRow(target: HTMLElement, name: string) {
	const row = Array.from(target.querySelectorAll('[role="option"]')).find((el) => el.textContent?.includes(name)) as
		| HTMLElement
		| undefined;
	expect(row, `expected a row for ${name}`).toBeTruthy();
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
});

describe('PluginsTab list row declutter', () => {
	it('shows only name, version and description in the list - no source/type/capability/count chips', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);

		expect(pane.textContent).toContain('Short Plugin');
		expect(pane.textContent).toContain('v1.0.0');
		expect(pane.textContent).toContain('A brief description.');

		// Chips that used to render in the list must be gone from it.
		expect(pane.textContent).not.toContain('MARKETPLACE');
		expect(pane.textContent).not.toContain('BACKEND');
		expect(pane.textContent).not.toContain('does-a-thing');
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

	it('reveals a "Show more" toggle only once the description actually overflows, and expands it in place', async () => {
		mounted = mount();
		await settle();

		const pane = listPane(mounted.target);
		const shortDescription = Array.from(pane.querySelectorAll('p')).find((el) =>
			el.textContent?.includes('A brief description.')
		) as HTMLElement;
		const longDescription = Array.from(pane.querySelectorAll('p')).find((el) =>
			el.textContent?.includes('deliberately long')
		) as HTMLElement;
		expect(shortDescription).toBeTruthy();
		expect(longDescription).toBeTruthy();

		// jsdom never lays text out, so scrollHeight/clientHeight default to 0
		// for both paragraphs - simulate the long one actually overflowing its
		// one-line clamp the way a real browser would report it.
		Object.defineProperty(longDescription, 'scrollHeight', { value: 40, configurable: true });
		Object.defineProperty(longDescription, 'clientHeight', { value: 16, configurable: true });
		Object.defineProperty(shortDescription, 'scrollHeight', { value: 16, configurable: true });
		Object.defineProperty(shortDescription, 'clientHeight', { value: 16, configurable: true });
		flushSync(() => window.dispatchEvent(new Event('resize')));
		await settle();

		expect(longDescription.className).toContain('line-clamp-1');

		const showMoreButtons = Array.from(pane.querySelectorAll('button')).filter(
			(b) => b.getAttribute('aria-label') === 'Show more'
		);
		expect(showMoreButtons.length).toBe(1);

		flushSync(() => showMoreButtons[0].click());
		await settle();

		expect(longDescription.className).not.toContain('line-clamp-1');
		const showLessButton = Array.from(pane.querySelectorAll('button')).find(
			(b) => b.getAttribute('aria-label') === 'Show less'
		);
		expect(showLessButton).toBeTruthy();
	});
});
