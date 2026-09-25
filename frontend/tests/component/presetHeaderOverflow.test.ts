// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { PresetInfo } from '$lib/types/api';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
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
	uninstallPreset: vi.fn().mockResolvedValue({ success: true })
}));
vi.mock('$lib/stores/confirm', () => ({
	confirmDialog: vi.fn().mockResolvedValue(true)
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
const adminApi = await import('$lib/services/admin-api');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: PresetsTab } = await import('../../src/routes/admin/components/PresetsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id: 'krea2',
		name: 'Krea-2',
		version: '1.0.0',
		description: '',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: true,
		recipes: [],
		...overrides
	};
}

function setUrl(search: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost/admin${search}`) }));
}

function mount(component: unknown) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props: {} });
	return {
		target,
		destroy: () => {
			instance.$destroy();
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
	document.body.innerHTML = '';
});

describe('Admin preset detail header overflow', () => {
	it('puts Uninstall in the header overflow menu, not as a standalone action, for an installed preset', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [preset()] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: preset() } as never);
		vi.mocked(api.api.getPresetConfiguration).mockResolvedValue({ success: true, data: { entries: [] } } as never);
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		expect(Array.from(mounted.target.querySelectorAll('button')).some((b) => b.textContent?.trim() === 'Uninstall')).toBe(
			false
		);

		const overflowTrigger = mounted.target.querySelector('button[aria-label="More actions"]') as HTMLButtonElement;
		expect(overflowTrigger, 'expected a header overflow trigger').toBeTruthy();

		flushSync(() => overflowTrigger.click());
		await settle();

		const uninstallItem = Array.from(mounted.target.querySelectorAll('[role="menuitem"]')).find(
			(el) => el.textContent?.trim() === 'Uninstall'
		) as HTMLButtonElement;
		expect(uninstallItem, 'expected an Uninstall menu item').toBeTruthy();

		flushSync(() => uninstallItem.click());
		await settle();

		expect(adminApi.uninstallPreset).toHaveBeenCalledWith('krea2');
	});
});
