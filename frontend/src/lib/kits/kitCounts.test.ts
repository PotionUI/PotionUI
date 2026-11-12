import { describe, expect, it } from 'vitest';
import { makeItem } from './fixtures';
import {
	acceptedFrom,
	acceptedSentence,
	countByKind,
	isTaken,
	mediaCountOf,
	presentKinds,
	skippedGroups,
	takesLabel,
	totalOf,
	usageNote,
	usageOf
} from './kitCounts';

const items = [
	makeItem('image', 'face'),
	makeItem('image', 'hat'),
	makeItem('video', 'walk'),
	makeItem('audio', 'voice'),
	makeItem('text', 'look')
];

describe('counts', () => {
	it('counts by kind and lists the kinds in the fixed order', () => {
		const counts = countByKind(items);
		expect(counts).toEqual({ image: 2, video: 1, audio: 1, text: 1 });
		expect(presentKinds(counts)).toEqual(['image', 'video', 'audio', 'text']);
		expect(totalOf(counts)).toBe(5);
	});
});

describe('what a model takes', () => {
	it('always takes text', () => {
		expect(isTaken('text', acceptedFrom([]))).toBe(true);
	});

	it('takes everything when the model is unknown', () => {
		expect(isTaken('audio', null)).toBe(true);
	});

	it('skips a kind the model does not list', () => {
		const accepted = acceptedFrom(['image', 'video']);
		expect(isTaken('audio', accepted)).toBe(false);
		const usage = usageOf(items, accepted);
		expect(usage.used.map((item) => item.name)).toEqual(['face', 'hat', 'walk', 'look']);
		expect(usage.skipped.map((item) => item.name)).toEqual(['voice']);
	});

	it('skips an item whose file was deleted', () => {
		const usage = usageOf([makeItem('image', 'gone', { missing: true })], null);
		expect(usage.skipped).toHaveLength(1);
	});

	it('counts media items against the field limit and leaves text out', () => {
		expect(mediaCountOf(items, acceptedFrom(['image', 'video']))).toBe(3);
		expect(mediaCountOf(items, null)).toBe(4);
	});

	it('words the note and the n of m', () => {
		const all = usageOf(items, null);
		expect(usageNote(all)).toEqual({ tone: 'success', text: 'All used' });
		expect(takesLabel(all, 5)).toBe('5');
		const part = usageOf(items, acceptedFrom(['image']));
		expect(usageNote(part)).toEqual({ tone: 'warning', text: '2 skipped' });
		expect(takesLabel(part, 5)).toBe('3 of 5');
	});

	it('groups what is skipped by kind', () => {
		const groups = skippedGroups(items, acceptedFrom(['image']));
		expect(groups.map((group) => [group.kind, group.names])).toEqual([
			['video', ['walk']],
			['audio', ['voice']]
		]);
	});

	it('describes what the model takes', () => {
		expect(acceptedSentence(acceptedFrom(['image', 'video', 'audio']))).toBe(
			'This model takes images, videos and audio. Text is added to the prompt where you call it.'
		);
		expect(acceptedSentence(acceptedFrom(['image']))).toContain('takes images.');
		expect(acceptedSentence(acceptedFrom([]))).toContain('no reference media');
	});
});
