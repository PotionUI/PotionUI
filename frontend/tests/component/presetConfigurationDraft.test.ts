import { describe, it, expect } from 'vitest';
import type { PresetConfigurationEntry } from '$lib/types/api';
import { valuesFrom, dirtyKeysOf, buildSavePayload } from '../../src/routes/admin/components/presetConfigurationDraft';

function entry(overrides: Partial<PresetConfigurationEntry> = {}): PresetConfigurationEntry {
	return { key: 'model_tags', type: 'model_tags', label: 'Model tags', value: ['a', 'b'], ...overrides };
}

describe('presetConfigurationDraft', () => {
	it('maps entries to a key/value record', () => {
		expect(valuesFrom([entry({ key: 'a', value: ['1'] }), entry({ key: 'b', value: ['2'] })])).toEqual({
			a: ['1'],
			b: ['2']
		});
	});

	it('finds no dirty keys when pending matches original', () => {
		const entries = [entry({ key: 'a' }), entry({ key: 'b' })];
		const original = valuesFrom(entries);
		const pending = valuesFrom(entries);
		expect(dirtyKeysOf(entries, pending, original)).toEqual([]);
	});

	it('flags only the entries whose pending value changed', () => {
		const entries = [entry({ key: 'a', value: ['1'] }), entry({ key: 'b', value: ['2'] })];
		const original = valuesFrom(entries);
		const pending = { ...original, a: ['1', '3'] };
		expect(dirtyKeysOf(entries, pending, original)).toEqual(['a']);
	});

	it('treats reordering an array value as dirty (structural, not set, comparison)', () => {
		const entries = [entry({ key: 'a', value: ['1', '2'] })];
		const original = valuesFrom(entries);
		const pending = { a: ['2', '1'] };
		expect(dirtyKeysOf(entries, pending, original)).toEqual(['a']);
	});

	it('builds a save payload containing only the dirty keys', () => {
		const pending = { a: ['1', '3'], b: ['2'], c: ['unchanged'] };
		expect(buildSavePayload(['a'], pending)).toEqual({ a: ['1', '3'] });
	});
});
