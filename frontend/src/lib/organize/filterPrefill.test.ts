import { describe, expect, it, vi } from 'vitest';

const getModels = vi.fn();
vi.mock('$lib/services/api/index', () => ({ api: { getModels: (...a: unknown[]) => getModels(...a) } }));

import {
	hasConvertibleHistoryFilters,
	hasConvertibleLibraryFilters,
	historyFilterRule,
	libraryFilterRule,
	resolveModelIdByName
} from './filterPrefill';
import type { GenerationHistoryFilters } from '$lib/types/history';

const base: GenerationHistoryFilters = {
	status: 'all',
	datePreset: 'all',
	selectedTagIds: [],
	mediaType: 'all',
	search: '',
	searchMode: 'keyword'
};

const tagNames = (ids: string[]) => ids.map((id) => `name-${id}`);

describe('history filters to rule', () => {
	it('is not convertible without a model, kind, preset, mode or tag', () => {
		expect(hasConvertibleHistoryFilters({ ...base, status: 'completed', minRating: 3 })).toBe(false);
		expect(hasConvertibleHistoryFilters({ ...base, mode: 'txt2img' })).toBe(true);
	});

	it('maps each filter to its fact', async () => {
		const rule = await historyFilterRule(
			{ ...base, modelName: 'Krea', mediaType: 'video', presetId: 'p1', mode: 'txt2vid', selectedTagIds: ['a', 'b'] },
			{ tagNames, resolveModelId: async () => 'm-1' }
		);
		expect(rule.subject).toBe('generation');
		expect(rule.conditions).toEqual([
			{ fact: 'model', operator: 'is', value: 'm-1' },
			{ fact: 'media_kind', operator: 'is', value: 'video' },
			{ fact: 'preset', operator: 'is', value: 'p1' },
			{ fact: 'mode', operator: 'is', value: 'txt2vid' },
			{ fact: 'tags', operator: 'has', value: ['name-a', 'name-b'] }
		]);
	});

	it('drops the model condition when the name cannot be resolved', async () => {
		const rule = await historyFilterRule(
			{ ...base, modelName: 'Gone', mode: 'img2img' },
			{ tagNames, resolveModelId: async () => null }
		);
		expect(rule.conditions).toEqual([{ fact: 'mode', operator: 'is', value: 'img2img' }]);
	});

	it('resolves a model id by exact display name only', async () => {
		getModels.mockResolvedValue({
			success: true,
			data: { models: [{ id: 'x', name: 'Krea 2 Turbo' }, { id: 'y', name: 'Krea' }] }
		});
		expect(await resolveModelIdByName('krea')).toBe('y');
		expect(getModels).toHaveBeenCalledWith({ search: 'krea', limit: 25 });
		getModels.mockResolvedValue({ success: true, data: { models: [{ id: 'x', name: 'Other' }] } });
		expect(await resolveModelIdByName('krea')).toBeNull();
		getModels.mockRejectedValue(new Error('down'));
		expect(await resolveModelIdByName('krea')).toBeNull();
	});
});

describe('library filters to rule', () => {
	const filters = { mediaType: 'all' as const, selectedTagIds: [], search: '' };

	it('is not convertible by search alone', () => {
		expect(hasConvertibleLibraryFilters({ ...filters, search: 'cat' })).toBe(false);
		expect(hasConvertibleLibraryFilters({ ...filters, mediaType: 'audio' })).toBe(true);
	});

	it('builds an upload rule', () => {
		const rule = libraryFilterRule({ ...filters, mediaType: 'video', selectedTagIds: ['t'] }, { tagNames });
		expect(rule).toEqual({
			subject: 'upload',
			conditions: [
				{ fact: 'media_kind', operator: 'is', value: 'video' },
				{ fact: 'tags', operator: 'has', value: ['name-t'] }
			]
		});
	});
});
