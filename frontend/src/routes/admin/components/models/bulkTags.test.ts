import { describe, expect, it } from 'vitest';
import {
	addChip,
	buildBulkTagsBody,
	hasBulkChanges,
	pendingSummary,
	removeChip,
	resultSummary,
	selectionRows,
	suggestTags,
	toggleRemoval
} from './bulkTags';

describe('chips', () => {
	it('adds trimmed values once, case-insensitively', () => {
		let chips = addChip([], '  anime ');
		chips = addChip(chips, 'Anime');
		chips = addChip(chips, '   ');
		chips = addChip(chips, 'portrait');
		expect(chips).toEqual(['anime', 'portrait']);
		expect(removeChip(chips, 'ANIME')).toEqual(['portrait']);
	});

	it('toggles removal ids', () => {
		expect(toggleRemoval([], 't1')).toEqual(['t1']);
		expect(toggleRemoval(['t1', 't2'], 't1')).toEqual(['t2']);
	});
});

describe('suggestTags', () => {
	const known = [
		{ id: '1', name: 'Anime' },
		{ id: '2', name: 'semi-anime' },
		{ id: '3', name: 'portrait' }
	];

	it('ranks prefix matches first and hides chipped tags', () => {
		expect(suggestTags(known, 'ani', []).map((t) => t.name)).toEqual(['Anime', 'semi-anime']);
		expect(suggestTags(known, 'ani', ['anime']).map((t) => t.name)).toEqual(['semi-anime']);
	});

	it('returns the head of the pool for an empty query', () => {
		expect(suggestTags(known, '', [], 2).map((t) => t.id)).toEqual(['1', '2']);
	});
});

describe('selectionRows', () => {
	it('merges duplicate ids, caps at the total and orders by count', () => {
		const rows = selectionRows(
			[
				{ id: 't2', name: 'style', count: 2 },
				{ id: 't1', name: 'anime', count: 3 },
				{ id: 't2', name: 'style', count: 1 },
				{ id: 't3', name: 'b-roll', count: 9 }
			],
			5
		);
		expect(rows.map((r) => [r.name, r.label, r.onAll])).toEqual([
			['b-roll', '5/5', true],
			['anime', '3/5', false],
			['style', '3/5', false]
		]);
	});
});

describe('buildBulkTagsBody', () => {
	const rows = selectionRows(
		[
			{ id: 't1', name: 'anime', count: 3 },
			{ id: 't2', name: 'style', count: 1 }
		],
		5
	);

	it('builds add names and remove ids, dropping stale and conflicting entries', () => {
		const body = buildBulkTagsBody(['m1', 'm2', 'm1'], ['portrait', 'Anime'], ['t1', 'gone'], rows);
		expect(body).toEqual({ model_ids: ['m1', 'm2'], add: ['portrait'], remove: ['t1'] });
		expect(hasBulkChanges(body)).toBe(true);
		expect(pendingSummary(body)).toBe('+1 to add · −1 to remove');
	});

	it('reports no change for an empty diff', () => {
		const body = buildBulkTagsBody(['m1'], [], [], rows);
		expect(hasBulkChanges(body)).toBe(false);
		expect(pendingSummary(body)).toBe('');
	});
});

describe('resultSummary', () => {
	it('sums per-tag counts', () => {
		expect(
			resultSummary({
				models: 3,
				unknown_model_ids: [],
				unknown_tags: [],
				tags: [
					{ id: 't1', name: 'anime', added: 2, already_present: 1, removed: 0 },
					{ id: 't2', name: 'style', added: 0, already_present: 0, removed: 1 }
				]
			})
		).toBe('Added 2 tags, removed 1 tag across 3 models');
	});

	it('mentions missing models and no-op runs', () => {
		expect(
			resultSummary({
				models: 1,
				unknown_model_ids: ['ghost'],
				unknown_tags: [],
				tags: [{ id: 't1', name: 'anime', added: 0, already_present: 1, removed: 0 }]
			})
		).toBe('No changes across 1 model · 1 model not found');
	});
});
