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
	it('appends the old keys after the field value in declared order and drops them', () => {
		const data = { media_inputs: [image], old_tracks: [audio], old_clips: [video], prompt: 'x' };
		expect(applyMergeFrom(schema, data)).toEqual({ media_inputs: [image, video, audio], prompt: 'x' });
		expect(data).toEqual({ media_inputs: [image], old_tracks: [audio], old_clips: [video], prompt: 'x' });
	});

	it('leaves data without old keys untouched', () => {
		const data = { media_inputs: [video, image], prompt: 'x' };
		expect(applyMergeFrom(schema, data)).toBe(data);
	});

	it('treats empty and single values as lists', () => {
		expect(applyMergeFrom(schema, { media_inputs: null, old_clips: video, old_tracks: '' })).toEqual({
			media_inputs: [video]
		});
	});

	it('passes null data through', () => {
		expect(applyMergeFrom(schema, null)).toBeNull();
	});
});

describe('marker rewrite on hydration', () => {
	it('points saved markers of merged keys at the field', () => {
		const data = { media_inputs: [image], old_clips: [video], notes: 'like @[old_clips:uploads/v.mp4] @[other:x]' };
		expect(applyMergeFrom(schema, data)).toEqual({
			media_inputs: [image, video],
			notes: 'like @[media_inputs:uploads/v.mp4] @[other:x]'
		});
	});

	it('rewrites markers even when no old values are present', () => {
		expect(applyMergeFrom(schema, { notes: '@[old_tracks:uploads/t.wav]' })).toEqual({
			notes: '@[media_inputs:uploads/t.wav]'
		});
	});

	it('maps every merged key to its field', () => {
		expect(schemaMergeFromAliases(schema)).toEqual({ old_clips: 'media_inputs', old_tracks: 'media_inputs' });
	});

	it('keeps identity when there is nothing to rewrite', () => {
		const value = { a: ['@[media_inputs:x]'], b: { field: 'other', item_key: 'y' } };
		expect(rewriteMergedMarkers(value, { old_clips: 'media_inputs' })).toBe(value);
	});
});
