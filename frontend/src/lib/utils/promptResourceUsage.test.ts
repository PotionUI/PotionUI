import { describe, expect, it } from 'vitest';
import { countResourceReferences, resourceUseCount, resourceUseCountsEqual, tabResourceSegmentGroups } from './promptResourceUsage';

describe('countResourceReferences', () => {
	it('counts every marker per field and item across segment groups', () => {
		const counts = countResourceReferences([
			[
				{ content: '@[references:a.png] meets @[references:b.png]' },
				{ content: 'again @[references:a.png] with @[reference_videos:clip.mp4]' }
			],
			[{ content: 'no @[references:a.png] here' }]
		]);
		expect(counts).toEqual({
			references: { 'a.png': 3, 'b.png': 1 },
			reference_videos: { 'clip.mp4': 1 }
		});
	});

	it('skips disabled segments and plain text tokens', () => {
		const counts = countResourceReferences([
			[
				{ content: '@[references:a.png]', enabled: false },
				{ content: '@[references:a.png]', isDisabled: true },
				{ content: '<Picture 1> and @[references:b.png]' }
			]
		]);
		expect(counts).toEqual({ references: { 'b.png': 1 } });
	});

	it('tolerates missing groups and empty content', () => {
		expect(countResourceReferences([undefined, null, [{ content: '' }, { content: null }]])).toEqual({});
	});
});

describe('tabResourceSegmentGroups', () => {
	it('uses the prompt tabs when the tab has them', () => {
		const tab = {
			promptSegments: [{ content: '@[references:top.png]' }],
			promptTabs: [
				{ promptSegments: [{ content: '@[references:a.png]' }], negativePromptSegments: [] },
				{ promptSegments: [{ content: '@[references:a.png]' }], negativePromptSegments: [] }
			]
		};
		expect(countResourceReferences(tabResourceSegmentGroups(tab))).toEqual({ references: { 'a.png': 2 } });
	});

	it('falls back to the positive and negative segments', () => {
		const tab = {
			promptSegments: [{ content: '@[references:a.png]' }],
			negativePromptSegments: [{ content: '@[references:a.png]' }],
			promptTabs: []
		};
		expect(countResourceReferences(tabResourceSegmentGroups(tab))).toEqual({ references: { 'a.png': 2 } });
	});
});

describe('resourceUseCount', () => {
	it('returns zero for unknown fields, items and missing keys', () => {
		const counts = { references: { 'a.png': 2 } };
		expect(resourceUseCount(counts, 'references', 'a.png')).toBe(2);
		expect(resourceUseCount(counts, 'references', 'b.png')).toBe(0);
		expect(resourceUseCount(counts, 'reference_videos', 'a.png')).toBe(0);
		expect(resourceUseCount(counts, 'references', null)).toBe(0);
	});
});

describe('resourceUseCountsEqual', () => {
	it('treats freshly counted but identical tallies as equal', () => {
		const a = countResourceReferences([[{ content: '@[refs:img-1] and @[refs:img-1] @[audio:a]' }]]);
		const b = countResourceReferences([[{ content: '@[audio:a] @[refs:img-1] then @[refs:img-1]' }]]);
		expect(a).not.toBe(b);
		expect(resourceUseCountsEqual(a, b)).toBe(true);
	});

	it('spots a changed count, a new item and a dropped field', () => {
		const base = { refs: { 'img-1': 2 } };
		expect(resourceUseCountsEqual(base, { refs: { 'img-1': 1 } })).toBe(false);
		expect(resourceUseCountsEqual(base, { refs: { 'img-1': 2, 'img-2': 1 } })).toBe(false);
		expect(resourceUseCountsEqual(base, {})).toBe(false);
		expect(resourceUseCountsEqual({}, base)).toBe(false);
		expect(resourceUseCountsEqual({ refs: { 'img-1': 2 } }, { audio: { 'img-1': 2 } })).toBe(false);
	});
});
