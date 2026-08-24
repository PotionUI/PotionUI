import { describe, expect, it } from 'vitest';
import type { ChipData, Segment } from '$lib/types/segments';
import {
	formatSegmentIndex,
	segmentCharCount,
	segmentDisplayName,
	segmentFooterActions
} from './segmentFooter';

function editor(id: string, content: string, partial: Partial<Segment> = {}): Segment {
	return { id, content, type: 'content', chips: {}, enabled: true, ...partial };
}

function chip(id: string, value: string): ChipData {
	return {
		id,
		categoryPath: 'people.role',
		valueId: 'v1',
		label: value,
		value,
		allValues: [],
		shuffle: false,
		autoRegen: false
	};
}

describe('segmentFooterActions', () => {
	it('offers the same actions in the same order regardless of card state', () => {
		const states = [
			editor('a', 'x', { name: 'SUBJECT' }),
			editor('b', 'x'),
			editor('c', 'x', { enabled: false }),
			editor('d', 'x', { name: 'SUBJECT', description: 'notes' })
		];

		for (const segment of states) {
			expect(segmentFooterActions(segment).map((action) => action.id)).toEqual([
				'toggleDisabled',
				'duplicate',
				'editDetails',
				'saveAsSegment'
			]);
		}
	});

	it('names the first action Disable while the segment is enabled', () => {
		const [first] = segmentFooterActions(editor('a', 'x'));
		expect(first.label).toBe('Disable');
		expect(first.icon).toBe('eye-off');
	});

	it('names the first action Enable once the segment is disabled', () => {
		const [first] = segmentFooterActions(editor('a', 'x', { enabled: false }));
		expect(first.label).toBe('Enable');
		expect(first.icon).toBe('eyes');
	});

	it('reads a legacy isDisabled-only segment as disabled', () => {
		const [first] = segmentFooterActions(editor('a', 'x', { enabled: undefined, isDisabled: true }));
		expect(first.label).toBe('Enable');
	});

	it('leaves every action but the first identical between enabled and disabled', () => {
		const enabled = segmentFooterActions(editor('a', 'x')).slice(1);
		const disabled = segmentFooterActions(editor('a', 'x', { enabled: false })).slice(1);
		expect(disabled).toEqual(enabled);
	});

	it('labels every action in sentence case', () => {
		const labels = [
			...segmentFooterActions(editor('a', 'x')),
			...segmentFooterActions(editor('b', 'x', { enabled: false }))
		].map((action) => action.label);

		for (const label of labels) {
			expect(label).toBe(label.charAt(0).toUpperCase() + label.slice(1).toLowerCase());
		}
	});
});

describe('segmentCharCount', () => {
	it('counts the plain content when the card holds no chips', () => {
		expect(segmentCharCount(editor('a', 'harsh noon sun, hard shadows'))).toBe(28);
	});

	it('counts the chip’s resolved value, not the marker that stands in for it', () => {
		const segment = editor('a', 'a #people.role at dusk', {
			chips: { c1: chip('c1', 'lighthouse keeper') }
		});

		expect(segmentCharCount(segment)).toBe('a lighthouse keeper at dusk'.length);
	});

	it('still reports a disabled card’s own length', () => {
		const segment = editor('a', 'harsh noon sun', { enabled: false });
		expect(segmentCharCount(segment)).toBe('harsh noon sun'.length);
	});

	it('is zero for a blank card', () => {
		expect(segmentCharCount(editor('a', ''))).toBe(0);
	});
});

describe('formatSegmentIndex', () => {
	it('numbers from one, zero-padded for the aligned gutter', () => {
		expect(formatSegmentIndex(0)).toBe('01');
		expect(formatSegmentIndex(1)).toBe('02');
		expect(formatSegmentIndex(11)).toBe('12');
	});

	it('does not truncate past two digits', () => {
		expect(formatSegmentIndex(99)).toBe('100');
	});
});

describe('segmentDisplayName', () => {
	it('trims the name and falls back to the legacy title field', () => {
		expect(segmentDisplayName({ name: '  SUBJECT  ' })).toBe('SUBJECT');
		expect(segmentDisplayName({ name: null, title: 'Legacy' })).toBe('Legacy');
	});

	it('is null when there is nothing to show', () => {
		expect(segmentDisplayName({ name: '   ' })).toBeNull();
		expect(segmentDisplayName({})).toBeNull();
	});
});
