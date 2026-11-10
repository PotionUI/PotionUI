import { describe, it, expect } from 'vitest';
import type { PresetInfo } from '$lib/types/api';
import {
	isUnavailableMirror,
	mirrorCountLabel,
	mirrorSourceName,
	mirrorStateCopy,
	presetHeaderChips
} from './presetMirrorState';

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return { id: 'krea2', name: 'Krea-2', version: '1.0.0', tags: [], engine: 'native', installed: true, ...overrides };
}

describe('mirrorStateCopy', () => {
	it('has no copy for a file preset or a working mirror', () => {
		expect(mirrorStateCopy(null)).toBeNull();
		expect(mirrorStateCopy('ok')).toBeNull();
	});

	it('explains every broken state in plain words', () => {
		for (const state of ['source_missing', 'modes_missing', 'id_collision'] as const) {
			const copy = mirrorStateCopy(state);
			expect(copy?.label).toBeTruthy();
			expect(copy?.description).toBeTruthy();
			expect(copy?.description).not.toMatch(/source_missing|modes_missing|id_collision/);
		}
	});
});

describe('isUnavailableMirror', () => {
	it('is true only for a mirror in a broken state', () => {
		expect(isUnavailableMirror(preset())).toBe(false);
		expect(isUnavailableMirror(preset({ is_mirror: true, mirror_state: 'ok' }))).toBe(false);
		expect(isUnavailableMirror(preset({ is_mirror: true, mirror_state: 'source_missing' }))).toBe(true);
		expect(isUnavailableMirror(null)).toBe(false);
	});
});

describe('mirrorSourceName', () => {
	const byId = new Map([['krea2', { name: 'Realistic' }]]);

	it('uses the source preset’s current name', () => {
		expect(mirrorSourceName(preset({ id: 'm1', is_mirror: true, source_preset_id: 'krea2' }), byId)).toBe('Realistic');
	});

	it('falls back to the source id when the source is gone', () => {
		expect(mirrorSourceName(preset({ id: 'm1', is_mirror: true, source_preset_id: 'gone' }), byId)).toBe('gone');
	});

	it('is null for a file preset', () => {
		expect(mirrorSourceName(preset(), byId)).toBeNull();
	});
});

describe('mirrorCountLabel', () => {
	it('counts mirrors and hides zero', () => {
		expect(mirrorCountLabel(0)).toBeNull();
		expect(mirrorCountLabel(undefined)).toBeNull();
		expect(mirrorCountLabel(1)).toBe('1 mirror');
		expect(mirrorCountLabel(3)).toBe('3 mirrors');
	});
});

describe('presetHeaderChips', () => {
	it('keeps engine, version and install state for a file preset', () => {
		expect(presetHeaderChips(preset()).map((chip) => chip.label)).toEqual(['native', 'v1.0.0', 'Installed']);
	});

	it('marks a working mirror', () => {
		expect(presetHeaderChips(preset({ is_mirror: true, mirror_state: 'ok' })).map((chip) => chip.label)).toEqual([
			'native',
			'Mirror',
			'Installed'
		]);
	});

	it('shows the broken state instead of the install state', () => {
		const chips = presetHeaderChips(preset({ engine: undefined, version: '', is_mirror: true, mirror_state: 'source_missing' }));
		expect(chips.map((chip) => chip.key)).toEqual(['mirror', 'mirror-state']);
		expect(chips[1].tone).toBe('warning');
	});
});
