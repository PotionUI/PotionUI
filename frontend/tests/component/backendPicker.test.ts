// @vitest-environment jsdom
//
// BackendPicker (generate page): shown only when the preset's engine has more
// than one enabled backend, renders "Automatic" plus one row per candidate
// backend with an eligibility chip from the requirements `backends[]`
// summary, and selecting a row writes `selectedBackendId` onto the tab (or
// clears it back to `null` for Automatic).
import { describe, it, expect, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';
import type { RequirementBackendInfo } from '$lib/types/api';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetRequirements: vi.fn() }
}));

const api = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: BackendPicker } = await import(
	'../../src/routes/generate/components/BackendPicker.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const PRESET_ID = 'preset-1';

function backend(overrides: Partial<RequirementBackendInfo> = {}): RequirementBackendInfo {
	return {
		id: 'b1',
		name: 'Local ComfyUI',
		is_default: true,
		summary: { ok: 3, missing: 0, unknown: 0, optional_missing: 0 },
		...overrides
	};
}

function mockResponse(backends: RequirementBackendInfo[]) {
	return {
		success: true,
		data: { results: [], summary: { ok: 0, missing: 0, unknown: 0, optional_missing: 0 }, checked_at: 0, backends }
	};
}

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: BackendPicker as never,
		target,
		props: { tabId, presetId: PRESET_ID, ...props }
	});
	return {
		target,
		rows: () => Array.from(target.querySelectorAll<HTMLButtonElement>('button[role="option"]')),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let tabId: string;
let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	if (tabId) tabsStore.removeTab(tabId);
	vi.clearAllMocks();
});

describe('BackendPicker', () => {
	it('renders nothing for a single enabled backend', async () => {
		tabId = tabsStore.addTabWithData('Tab 1', { selectedPreset: PRESET_ID });
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse([backend()]));

		mounted = mount({});
		await settle();

		expect(mounted.target.textContent?.trim()).toBe('');
	});

	it('renders Automatic plus one row per backend with an eligibility chip, ok/missing/unknown', async () => {
		tabId = tabsStore.addTabWithData('Tab 1', { selectedPreset: PRESET_ID });
		const backends = [
			backend({ id: 'b1', name: 'Local ComfyUI', is_default: true, summary: { ok: 3, missing: 0, unknown: 0, optional_missing: 0 } }),
			backend({ id: 'b2', name: 'Remote ComfyUI', is_default: false, summary: { ok: 1, missing: 2, unknown: 0, optional_missing: 0 } })
		];
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse(backends));

		mounted = mount({});
		await settle();

		expect(mounted.target.textContent).toContain('Automatic');
		expect(mounted.target.textContent).toContain('Local ComfyUI');
		expect(mounted.target.textContent).toContain('default');
		expect(mounted.target.textContent).toContain('Remote ComfyUI');
		expect(mounted.target.textContent).toContain('2 missing');
		expect(mounted.target.textContent).toContain('ok');

		const rows = mounted.rows();
		expect(rows).toHaveLength(3);
	});

	it('selecting a backend row writes selectedBackendId on the tab', async () => {
		tabId = tabsStore.addTabWithData('Tab 1', { selectedPreset: PRESET_ID });
		const backends = [
			backend({ id: 'b1', name: 'Local ComfyUI', is_default: true }),
			backend({ id: 'b2', name: 'Remote ComfyUI', is_default: false, summary: { ok: 1, missing: 1, unknown: 0, optional_missing: 0 } })
		];
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse(backends));

		mounted = mount({ selectedBackendId: null });
		await settle();

		const remoteRow = mounted.rows().find((r) => r.textContent?.includes('Remote ComfyUI'));
		remoteRow?.click();
		await settle();

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId);
		expect(tab?.selectedBackendId).toBe('b2');
	});

	it('selecting Automatic clears selectedBackendId back to null', async () => {
		tabId = tabsStore.addTabWithData('Tab 1', { selectedPreset: PRESET_ID, selectedBackendId: 'b2' });
		const backends = [
			backend({ id: 'b1', name: 'Local ComfyUI', is_default: true }),
			backend({ id: 'b2', name: 'Remote ComfyUI', is_default: false })
		];
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse(backends));

		mounted = mount({ selectedBackendId: 'b2' });
		await settle();

		const automaticRow = mounted.rows().find((r) => r.textContent?.includes('Automatic'));
		automaticRow?.click();
		await settle();

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId);
		expect(tab?.selectedBackendId).toBeNull();
	});

	it('reports eligibility via onEligibilityChange as backends resolve and change', async () => {
		tabId = tabsStore.addTabWithData('Tab 1', { selectedPreset: PRESET_ID });
		vi.mocked(api.api.getPresetRequirements).mockResolvedValue(mockResponse([backend()]));

		const onEligibilityChange = vi.fn();
		mounted = mount({ onEligibilityChange });
		await settle();

		expect(onEligibilityChange).toHaveBeenLastCalledWith(false);
	});
});
