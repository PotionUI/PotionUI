import { describe, expect, it, vi, beforeEach } from 'vitest';
import type { ChipData } from '$lib/types/segments';

const searchPhrasebook = vi.fn();
vi.mock('$lib/services/api/index', () => ({
	api: { searchPhrasebook: (...args: unknown[]) => searchPhrasebook(...args) }
}));

import {
	applySegmentUpdate,
	locateSegmentIndex,
	mergeChipSelections,
	resolvePromptSegments
} from './promptSegments';

function makeChip(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: overrides.id ?? 'chip-1',
		categoryPath: overrides.categoryPath ?? 'style.lighting',
		valueId: overrides.valueId ?? 'val-1',
		label: overrides.label ?? 'Soft light',
		value: overrides.value ?? 'soft light',
		allValues: overrides.allValues ?? [],
		shuffle: overrides.shuffle ?? false,
		autoRegen: overrides.autoRegen ?? false
	};
}

describe('resolvePromptSegments', () => {
	it('joins enabled content and preserves breaks', () => {
		expect(
			resolvePromptSegments([
				{ id: 'one', content: 'portrait' },
				{ id: 'break', content: '', type: 'break' },
				{ id: 'two', content: 'studio light' }
			])
		).toBe('portrait BREAK studio light');
	});

	it('omits disabled segments but treats collapsed state as presentation-only', () => {
		expect(
			resolvePromptSegments([
				{ id: 'one', content: 'visible' },
				{ id: 'two', content: 'disabled', isDisabled: true },
				{ id: 'three', content: 'collapsed', isCollapsed: true }
			])
		).toBe('visible, collapsed');
	});

	it('joins with a blank line instead of a comma when the paragraph join is requested', () => {
		expect(
			resolvePromptSegments(
				[
					{ id: 'verse', content: '[Verse]\nrain on the window' },
					{ id: 'chorus', content: '[Chorus]\nnowhere to go' }
				],
				'paragraph'
			)
		).toBe('[Verse]\nrain on the window\n\n[Chorus]\nnowhere to go');
	});
});

describe('locateSegmentIndex', () => {
	const segments = [{ id: 'a', content: '' }, { id: 'b', content: '' }, { id: 'c', content: '' }];

	it('matches by id even when the proposed index is stale', () => {
		expect(locateSegmentIndex(segments, { segmentId: 'c', segmentIndex: 0 })).toBe(2);
	});

	it('falls back to the index when the id is not found', () => {
		expect(locateSegmentIndex(segments, { segmentId: 'missing', segmentIndex: 1 })).toBe(1);
	});

	it('returns -1 when neither the id nor the index resolve (e.g. an empty array)', () => {
		expect(locateSegmentIndex([], { segmentId: 'missing', segmentIndex: 0 })).toBe(-1);
		expect(locateSegmentIndex(segments, { segmentId: 'missing', segmentIndex: 99 })).toBe(-1);
	});
});

describe('mergeChipSelections', () => {
	it('carries the prior value/shuffle/autoRegen onto a re-parsed chip with the same category path', () => {
		const oldChips = {
			'old-1': makeChip({ id: 'old-1', categoryPath: 'style.lighting', valueId: 'val-dramatic', label: 'Dramatic', value: 'dramatic light', shuffle: true, autoRegen: true })
		};
		const newChips = {
			'new-1': makeChip({ id: 'new-1', categoryPath: 'style.lighting', valueId: 'val-default', label: 'Soft light', value: 'soft light', shuffle: false, autoRegen: false })
		};

		const merged = mergeChipSelections(oldChips, newChips);

		expect(merged['new-1']).toMatchObject({
			valueId: 'val-dramatic',
			label: 'Dramatic',
			value: 'dramatic light',
			shuffle: true,
			autoRegen: true
		});
	});

	it('leaves a newly introduced marker (no matching category path) as freshly parsed', () => {
		const oldChips = { 'old-1': makeChip({ id: 'old-1', categoryPath: 'style.lighting' }) };
		const newChips = { 'new-1': makeChip({ id: 'new-1', categoryPath: 'style.camera-angle' }) };

		expect(mergeChipSelections(oldChips, newChips)).toEqual(newChips);
	});

	it('consumes each old chip at most once when multiple new markers share a category path', () => {
		const oldChips = {
			'old-1': makeChip({ id: 'old-1', categoryPath: 'style.lighting', valueId: 'val-a', label: 'A' }),
			'old-2': makeChip({ id: 'old-2', categoryPath: 'style.lighting', valueId: 'val-b', label: 'B' })
		};
		const newChips = {
			'new-1': makeChip({ id: 'new-1', categoryPath: 'style.lighting', valueId: 'val-default' }),
			'new-2': makeChip({ id: 'new-2', categoryPath: 'style.lighting', valueId: 'val-default' })
		};

		const merged = mergeChipSelections(oldChips, newChips);

		expect([merged['new-1'].valueId, merged['new-2'].valueId].sort()).toEqual(['val-a', 'val-b']);
	});
});

describe('applySegmentUpdate', () => {
	beforeEach(() => {
		searchPhrasebook.mockReset();
	});

	it('returns null and leaves the source array untouched when the target segment cannot be resolved', async () => {
		const segments = [{ id: 'a', content: 'one' }];

		const result = await applySegmentUpdate(segments, {
			segmentId: 'missing',
			segmentIndex: 99,
			content: 'two'
		});

		expect(result).toBeNull();
		expect(searchPhrasebook).not.toHaveBeenCalled();
		expect(segments).toEqual([{ id: 'a', content: 'one' }]);
	});

	it('writes the new content on a plain-text update without touching other segments or chips', async () => {
		const segments = [
			{ id: 'a', content: 'portrait' },
			{ id: 'b', content: 'studio light' }
		];

		const result = await applySegmentUpdate(segments, {
			segmentId: 'b',
			segmentIndex: 1,
			content: 'dramatic light'
		});

		expect(searchPhrasebook).not.toHaveBeenCalled();
		expect(result).toEqual({
			index: 1,
			segments: [
				{ id: 'a', content: 'portrait' },
				{ id: 'b', content: 'dramatic light', chips: {} }
			]
		});
		// the input array is never mutated in place
		expect(segments[1].content).toBe('studio light');
	});

	it('hydrates a #category.path marker in the new content and carries forward the prior chip choice', async () => {
		searchPhrasebook.mockResolvedValue({
			success: true,
			data: {
				values: [
					{ id: 'val-default', label: 'Soft light', value: 'soft light' },
					{ id: 'val-dramatic', label: 'Dramatic', value: 'dramatic light' }
				]
			}
		});
		const oldChip = makeChip({
			id: 'old-1',
			categoryPath: 'style.lighting',
			valueId: 'val-dramatic',
			label: 'Dramatic',
			value: 'dramatic light',
			shuffle: true
		});
		const segments = [{ id: 'a', content: '#style.lighting', chips: { 'old-1': oldChip } }];

		const result = await applySegmentUpdate(segments, {
			segmentId: 'a',
			segmentIndex: 0,
			content: '#style.lighting'
		});

		expect(searchPhrasebook).toHaveBeenCalledWith('style.lighting');
		expect(result?.index).toBe(0);
		const chips = result?.segments[0].chips as Record<string, ChipData>;
		expect(Object.keys(chips)).toHaveLength(1);
		const chip = Object.values(chips)[0];
		expect(chip).toMatchObject({
			categoryPath: 'style.lighting',
			// the freshly parsed chip carries the user's prior selection forward
			valueId: 'val-dramatic',
			label: 'Dramatic',
			value: 'dramatic light',
			shuffle: true
		});
	});
});
