import { describe, expect, it } from 'vitest';
import {
	deriveContextStripModel,
	deriveTabSwitchDivider,
	presetLabelFor,
	type ContextStripTabInfo
} from './contextStrip';

function tab(overrides: Partial<ContextStripTabInfo> = {}): ContextStripTabInfo {
	return {
		id: 'tab-1',
		name: 'Portraits',
		selectedPreset: 'preset-krea2',
		selectedMode: 'txt2img',
		formData: { width: 1216, height: 832, steps: 28 },
		...overrides
	};
}

const presetName = (id: string) => (id === 'preset-krea2' ? 'Krea-2 Turbo' : null);

describe('presetLabelFor', () => {
	it('joins preset name and mode', () => {
		expect(presetLabelFor('Krea-2 Turbo', 'txt2img')).toBe('Krea-2 Turbo · txt2img');
	});

	it('falls back to preset name alone when no mode', () => {
		expect(presetLabelFor('Krea-2 Turbo', null)).toBe('Krea-2 Turbo');
	});

	it('is null when the preset name is unresolved', () => {
		expect(presetLabelFor(null, 'txt2img')).toBeNull();
	});
});

describe('deriveContextStripModel', () => {
	it('following: reports the active tab when nothing is pinned', () => {
		const model = deriveContextStripModel({
			activeTab: tab({ name: 'Portraits' }),
			pinnedTab: null,
			pinnedTabId: null,
			presetName
		});
		expect(model).toEqual({
			state: 'following',
			tabName: 'Portraits',
			presetLabel: 'Krea-2 Turbo · txt2img',
			dims: '1216×832',
			steps: 28,
			activeTabName: null
		});
	});

	it('pinned-active: pinned tab matches the active tab', () => {
		const pinned = tab({ id: 'tab-1', name: 'Portraits' });
		const model = deriveContextStripModel({
			activeTab: tab({ id: 'tab-1', name: 'Portraits' }),
			pinnedTab: pinned,
			pinnedTabId: 'tab-1',
			presetName
		});
		expect(model?.state).toBe('pinned-active');
		expect(model?.tabName).toBe('Portraits');
		expect(model?.activeTabName).toBeNull();
	});

	it('pinned-mismatch: pinned tab differs from the active tab', () => {
		const pinned = tab({
			id: 'tab-2',
			name: 'Krea-2 test',
			selectedPreset: 'preset-krea2',
			selectedMode: 'txt2img',
			formData: { width: 896, height: 1152, steps: 32 }
		});
		const active = tab({ id: 'tab-1', name: 'Portraits' });
		const model = deriveContextStripModel({
			activeTab: active,
			pinnedTab: pinned,
			pinnedTabId: 'tab-2',
			presetName
		});
		expect(model).toEqual({
			state: 'pinned-mismatch',
			tabName: 'Krea-2 test',
			presetLabel: 'Krea-2 Turbo · txt2img',
			dims: '896×1152',
			steps: 32,
			activeTabName: 'Portraits'
		});
	});

	it('pinned-mismatch: still reports mismatch when the active tab is unknown', () => {
		const pinned = tab({ id: 'tab-2', name: 'Krea-2 test' });
		const model = deriveContextStripModel({
			activeTab: null,
			pinnedTab: pinned,
			pinnedTabId: 'tab-2',
			presetName
		});
		expect(model?.state).toBe('pinned-mismatch');
		expect(model?.activeTabName).toBeNull();
	});

	it('degrades to following when pinnedTabId is set but the tab no longer resolves', () => {
		const active = tab({ id: 'tab-1', name: 'Portraits' });
		const model = deriveContextStripModel({
			activeTab: active,
			pinnedTab: null,
			pinnedTabId: 'stale-id',
			presetName
		});
		expect(model?.state).toBe('following');
		expect(model?.tabName).toBe('Portraits');
	});

	it('returns null when there is no tab to report on at all', () => {
		const model = deriveContextStripModel({
			activeTab: null,
			pinnedTab: null,
			pinnedTabId: null,
			presetName
		});
		expect(model).toBeNull();
	});

	it('missing preset: presetLabel is null when no preset is selected', () => {
		const model = deriveContextStripModel({
			activeTab: tab({ selectedPreset: null, selectedMode: null }),
			pinnedTab: null,
			pinnedTabId: null,
			presetName
		});
		expect(model?.presetLabel).toBeNull();
	});

	it('missing dims: dims and steps are null when the form has no usable width/height', () => {
		const model = deriveContextStripModel({
			activeTab: tab({ formData: {} }),
			pinnedTab: null,
			pinnedTabId: null,
			presetName
		});
		expect(model?.dims).toBeNull();
		expect(model?.steps).toBeNull();
	});

	it('missing dims: ignores non-positive or non-finite width/height/steps', () => {
		const model = deriveContextStripModel({
			activeTab: tab({ formData: { width: 0, height: -5, steps: Infinity } }),
			pinnedTab: null,
			pinnedTabId: null,
			presetName
		});
		expect(model?.dims).toBeNull();
		expect(model?.steps).toBeNull();
	});
});

describe('deriveTabSwitchDivider', () => {
	const activeTab = tab({ id: 'tab-2', name: 'Krea-2 test' });

	it('announces a followed-tab switch when there is history to anchor it after', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: 'tab-1',
			activeTab,
			pinnedTabId: null,
			hasMessages: true,
			presetName
		});
		expect(divider).toEqual({
			tabName: 'Krea-2 test',
			presetLabel: 'Krea-2 Turbo · txt2img',
			dims: '1216×832'
		});
	});

	it('is null when pinned — pinning is a deliberate override, not a "switch"', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: 'tab-1',
			activeTab,
			pinnedTabId: 'tab-2',
			hasMessages: true,
			presetName
		});
		expect(divider).toBeNull();
	});

	it('is null on the very first read (no previous tab tracked yet)', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: null,
			activeTab,
			pinnedTabId: null,
			hasMessages: true,
			presetName
		});
		expect(divider).toBeNull();
	});

	it('is null when the tab did not actually change', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: 'tab-2',
			activeTab,
			pinnedTabId: null,
			hasMessages: true,
			presetName
		});
		expect(divider).toBeNull();
	});

	it('is null when the transcript is empty — nothing to anchor the divider after', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: 'tab-1',
			activeTab,
			pinnedTabId: null,
			hasMessages: false,
			presetName
		});
		expect(divider).toBeNull();
	});

	it('is null when there is no active tab', () => {
		const divider = deriveTabSwitchDivider({
			previousTabId: 'tab-1',
			activeTab: null,
			pinnedTabId: null,
			hasMessages: true,
			presetName
		});
		expect(divider).toBeNull();
	});
});
