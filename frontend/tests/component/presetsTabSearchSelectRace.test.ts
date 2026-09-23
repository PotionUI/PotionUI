// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { PresetInfo } from '$lib/types/api';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>(
		'$lib/services/api/index'
	);
	return {
		...actual,
		api: {
			...actual.api,
			listPresets: vi.fn(),
			getPreset: vi.fn(),
			getPresetConfiguration: vi.fn()
		}
	};
});
vi.mock('$lib/services/admin-api', () => ({
	installPreset: vi.fn(),
	uninstallPreset: vi.fn()
}));
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

const api = await import('$lib/services/api/index');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: PresetsTab } = await import('../../src/routes/admin/components/PresetsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id: 'preset-a',
		name: 'Preset A',
		version: '1.0.0',
		description: 'list description',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: false,
		...overrides
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

function setUrl(search: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost/admin${search}`) }));
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PresetsTab as never, target, props: {} });
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

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	setUrl('');
});

describe('PresetsTab search/select race', () => {
	it('renders the newly-selected preset detail after navigating away mid-fetch', async () => {
		const presetA = preset({ id: 'preset-a', name: 'Preset A' });
		const presetB = preset({ id: 'preset-b', name: 'Preset B', description: 'list description b' });

		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [presetA, presetB] });

		const detailA = deferred<{ success: true; data: PresetInfo }>();
		const detailB = deferred<{ success: true; data: PresetInfo }>();
		vi.mocked(api.api.getPreset).mockImplementation((id: string) => {
			if (id === 'preset-a') return detailA.promise as never;
			if (id === 'preset-b') return detailB.promise as never;
			throw new Error(`unexpected id ${id}`);
		});

		setUrl('?tab=presets&id=preset-a');
		mounted = mount();
		await settle();

		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset A');

		setUrl('?tab=presets&id=preset-b');
		await settle();

		flushSync(() => {
			detailB.resolve({ success: true, data: { ...presetB, description: 'detail description b' } });
		});
		await settle();

		flushSync(() => {
			detailA.resolve({ success: true, data: { ...presetA, description: 'detail description a' } });
		});
		await settle();

		expect(mounted.target.textContent).not.toContain('Preset not found');
		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset B');
		expect(mounted.target.textContent).toContain('detail description b');
		expect(mounted.target.textContent).not.toContain('detail description a');
	});

	it('renders the detail once the initial fetch resolves', async () => {
		const presetA = preset({ id: 'preset-a', name: 'Preset A' });
		const presetB = preset({ id: 'preset-b', name: 'Preset B' });

		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [presetA, presetB] });

		const detailA = deferred<{ success: true; data: PresetInfo }>();
		vi.mocked(api.api.getPreset).mockImplementation((id: string) => {
			if (id === 'preset-a') return detailA.promise as never;
			throw new Error(`unexpected id ${id}`);
		});

		setUrl('?tab=presets&id=preset-a');
		mounted = mount();
		await settle();
		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset A');

		flushSync(() => {
			detailA.resolve({ success: true, data: { ...presetA, description: 'detail description a' } });
		});
		await settle();

		expect(mounted.target.textContent).not.toContain('Preset not found');
		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset A');
		expect(mounted.target.textContent).toContain('detail description a');
	});
});
