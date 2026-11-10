import { describe, it, expect } from 'vitest';
import type { PresetInfo } from '$lib/types/api';
import { presetDetailTabsFor, resolvePresetDetailTab, restorePresetDetailTab } from './presetDetailTabs';

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id: 'krea2',
		name: 'Krea-2',
		version: '1.0.0',
		description: '',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: false,
		...overrides
	};
}

describe('presetDetailTabsFor', () => {
	it('offers Overview, Access and Requirements for a preset that is not installed', () => {
		const ids = presetDetailTabsFor(preset(), true).map((tab) => tab.id);
		expect(ids).toEqual(['overview', 'access', 'requirements']);
	});

	it('adds Display, Form and Prompt text for an installed preset and Configuration only once it has entries', () => {
		const installed = preset({ installed: true });
		expect(presetDetailTabsFor(installed, false).map((tab) => tab.id)).toEqual([
			'overview',
			'access',
			'display',
			'form',
			'prompt',
			'requirements'
		]);
		expect(presetDetailTabsFor(installed, true).map((tab) => tab.id)).toEqual([
			'overview',
			'access',
			'display',
			'configuration',
			'form',
			'prompt',
			'requirements'
		]);
	});

	it('gives a working mirror the same tabs as any installed preset', () => {
		const mirror = preset({ installed: true, is_mirror: true, mirror_state: 'ok', source_preset_id: 'krea2' });
		expect(presetDetailTabsFor(mirror, true).map((tab) => tab.id)).toEqual([
			'overview',
			'access',
			'display',
			'configuration',
			'form',
			'prompt',
			'requirements'
		]);
	});

	it('keeps an unavailable mirror to Overview, Access and Display', () => {
		for (const state of ['source_missing', 'modes_missing', 'id_collision'] as const) {
			const mirror = preset({ installed: true, is_mirror: true, mirror_state: state, source_preset_id: 'krea2' });
			expect(presetDetailTabsFor(mirror, true).map((tab) => tab.id)).toEqual(['overview', 'access', 'display']);
		}
	});

	it('counts missing requirements on the Requirements tab', () => {
		const tabs = presetDetailTabsFor(
			preset({ requirements_summary: { ok: 1, missing: 2, unknown: 0, optional_missing: 0 } }),
			false
		);
		expect(tabs.find((tab) => tab.id === 'requirements')?.count).toBe(2);
	});
});

describe('restorePresetDetailTab', () => {
	it('reads every tab id out of the URL value', () => {
		for (const id of ['overview', 'access', 'display', 'configuration', 'form', 'prompt', 'requirements'] as const) {
			expect(restorePresetDetailTab(id)).toBe(id);
		}
	});

	it('falls back to Overview for a missing, unknown or empty value', () => {
		expect(restorePresetDetailTab(null)).toBe('overview');
		expect(restorePresetDetailTab('')).toBe('overview');
		expect(restorePresetDetailTab('nope')).toBe('overview');
	});
});

describe('resolvePresetDetailTab', () => {
	it('keeps a requested tab the preset offers', () => {
		const tabs = presetDetailTabsFor(preset({ installed: true }), true);
		expect(resolvePresetDetailTab(tabs, 'configuration')).toBe('configuration');
		expect(resolvePresetDetailTab(tabs, 'form')).toBe('form');
		expect(resolvePresetDetailTab(tabs, 'requirements')).toBe('requirements');
	});

	it('falls back to Overview for a tab the preset does not offer', () => {
		expect(resolvePresetDetailTab(presetDetailTabsFor(preset(), true), 'form')).toBe('overview');
		expect(resolvePresetDetailTab(presetDetailTabsFor(preset({ installed: true }), false), 'configuration')).toBe(
			'overview'
		);
	});

	it('falls back to Overview when there are no tabs yet', () => {
		expect(resolvePresetDetailTab([], 'requirements')).toBe('overview');
	});
});
