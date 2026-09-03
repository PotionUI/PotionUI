// @vitest-environment jsdom
//
// PresetsTab auto-selects the first catalog preset on load and kicks off a
// detail fetch for it. Narrowing the search before that fetch settles must
// not leave the detail pane stuck on "No preset selected" - neither when the
// search switches the selection to a different preset, nor when it simply
// keeps the already-selected preset in view.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { PresetInfo } from '$lib/types/api';

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

const api = await import('$lib/services/api/index');
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
});

describe('PresetsTab search/select race', () => {
	it('renders the newly-selected preset detail after narrowing the search mid-fetch', async () => {
		const presetA = preset({ id: 'preset-a', name: 'Preset A', tags: [] });
		const presetB = preset({ id: 'preset-b', name: 'Preset B', tags: ['fixture'], description: 'list description b' });

		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [presetA, presetB] });

		const detailA = deferred<{ success: true; data: PresetInfo }>();
		const detailB = deferred<{ success: true; data: PresetInfo }>();
		vi.mocked(api.api.getPreset).mockImplementation((id: string) => {
			if (id === 'preset-a') return detailA.promise as never;
			if (id === 'preset-b') return detailB.promise as never;
			throw new Error(`unexpected id ${id}`);
		});

		mounted = mount();
		await settle();

		// Default selection auto-fires for the first catalog preset and its
		// detail fetch is now in flight (detailA unresolved).
		expect(mounted.target.textContent).toContain('Preset A');

		// Narrow the search before that fetch resolves - this filters preset A
		// out and must reselect preset B, kicking off a second detail fetch.
		const input = mounted.target.querySelector<HTMLInputElement>('input[type="search"]');
		flushSync(() => {
			input!.value = 'fixture';
			input!.dispatchEvent(new Event('input', { bubbles: true }));
		});
		await settle();

		// The stale fetch for preset A settles after the reselect.
		flushSync(() => {
			detailA.resolve({ success: true, data: { ...presetA, description: 'detail description a' } });
		});
		await settle();

		flushSync(() => {
			detailB.resolve({ success: true, data: { ...presetB, description: 'detail description b' } });
		});
		await settle();

		expect(mounted.target.textContent).not.toContain('No preset selected');
		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset B');
		expect(mounted.target.textContent).toContain('detail description b');
		expect(mounted.target.textContent).not.toContain('detail description a');
	});

	it('renders the detail once the fetch resolves when the search keeps the selection in view', async () => {
		const presetA = preset({ id: 'preset-a', name: 'Preset A', tags: ['keepme'] });
		const presetB = preset({ id: 'preset-b', name: 'Preset B', tags: [] });

		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [presetA, presetB] });

		const detailA = deferred<{ success: true; data: PresetInfo }>();
		vi.mocked(api.api.getPreset).mockImplementation((id: string) => {
			if (id === 'preset-a') return detailA.promise as never;
			throw new Error(`unexpected id ${id}`);
		});

		mounted = mount();
		await settle();
		expect(mounted.target.textContent).toContain('Preset A');

		// A search that keeps the auto-selected preset in the filtered list -
		// the reselect effect must stay a no-op, and the pending fetch must
		// still land once it resolves.
		const input = mounted.target.querySelector<HTMLInputElement>('input[type="search"]');
		flushSync(() => {
			input!.value = 'keepme';
			input!.dispatchEvent(new Event('input', { bubbles: true }));
		});
		await settle();

		flushSync(() => {
			detailA.resolve({ success: true, data: { ...presetA, description: 'detail description a' } });
		});
		await settle();

		expect(mounted.target.textContent).not.toContain('No preset selected');
		expect(mounted.target.querySelector('h2')?.textContent).toBe('Preset A');
		expect(mounted.target.textContent).toContain('detail description a');
	});
});
