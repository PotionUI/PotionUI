import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { applyMergeFrom, rewriteMergedMarkers, schemaMergeFrom, schemaMergeFromAliases } from './mergeFrom';

const schema = {
	properties: {
		main: {
			children: [
				{
					type: 'tabs',
					children: [
						{
							type: 'tab',
							children: [
								{ name: 'media_inputs', type: 'media', merge_from: ['old_clips', 'old_tracks'] },
								{ name: 'prompt', type: 'text' }
							]
						}
					]
				}
			]
		}
	}
};

const image = { path: 'uploads/a.png', type: 'image' };
const video = { path: 'uploads/v.mp4', type: 'video' };
const audio = { path: 'uploads/t.wav', type: 'audio' };

describe('schemaMergeFrom', () => {
	it('finds merge_from on nested fields', () => {
		expect([...schemaMergeFrom(schema)]).toEqual([['media_inputs', ['old_clips', 'old_tracks']]]);
	});
});

describe('applyMergeFrom', () => {
	it('leaves data without old keys untouched', () => {
		const data = { media_inputs: [video, image], prompt: 'x' };
		expect(applyMergeFrom(schema, data)).toBe(data);
	});

	it('passes null data through', () => {
		expect(applyMergeFrom(schema, null)).toBeNull();
	});
});

describe('marker rewrite on hydration', () => {
	it('maps every merged key to its field', () => {
		expect(schemaMergeFromAliases(schema)).toEqual({ old_clips: 'media_inputs', old_tracks: 'media_inputs' });
	});

	it('keeps identity when there is nothing to rewrite', () => {
		const value = { a: ['@[media_inputs:x]'], b: { field: 'other', item_key: 'y' } };
		expect(rewriteMergedMarkers(value, { old_clips: 'media_inputs' })).toBe(value);
	});
});

interface SharedCase {
	name: string;
	fields: { name: string; merge_from?: string[] }[];
	data: Record<string, unknown>;
	expected: Record<string, unknown>;
}

const sharedCases: SharedCase[] = JSON.parse(
	readFileSync(new URL('../../../../tests/fixtures/merge_from_cases.json', import.meta.url), 'utf8')
).cases;

describe('shared merge_from cases', () => {
	it.each(sharedCases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
		const sharedSchema = { properties: { main: { children: c.fields } } };
		const input = structuredClone(c.data);

		expect(applyMergeFrom(sharedSchema, input)).toEqual(c.expected);
		expect(input).toEqual(c.data);
	});
});
