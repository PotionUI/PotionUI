import { describe, expect, it } from 'vitest';
import { splitPlainTextIntoSegments } from './promptComposer';

describe('splitPlainTextIntoSegments', () => {
	it('returns nothing for blank input', () => {
		expect(splitPlainTextIntoSegments('   \n  ')).toEqual([]);
	});

	it('keeps a single-line prompt as one segment', () => {
		expect(splitPlainTextIntoSegments('a lone wolf howling at the moon')).toEqual([
			'a lone wolf howling at the moon'
		]);
	});

	it('splits on blank lines first', () => {
		expect(splitPlainTextIntoSegments('subject here\n\nlighting and camera\n\nstyle notes')).toEqual([
			'subject here',
			'lighting and camera',
			'style notes'
		]);
	});

	it('falls back to one segment per non-empty line when there is no blank-line boundary', () => {
		expect(splitPlainTextIntoSegments('subject here\nlighting and camera\nstyle notes')).toEqual([
			'subject here',
			'lighting and camera',
			'style notes'
		]);
	});

	it('drops stray blank lines inside a blank-line-separated paste', () => {
		expect(splitPlainTextIntoSegments('first\n\n\nsecond')).toEqual(['first', 'second']);
	});
});
