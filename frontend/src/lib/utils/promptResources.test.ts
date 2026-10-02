import { describe, expect, it } from 'vitest';
import {
	collectResourceProblems,
	deriveResourcesFromText,
	encodeResourceMarker,
	findResourceProblems,
	findResourceSpec,
	findSpecByGroupKey,
	itemAtPosition,
	itemPosition,
	kindLabel,
	mediaFieldItems,
	mediaItemKey,
	parseResourceMarker,
	resolveResourceMarkers,
	resourceGroupKey,
	resourceGroupLabel,
	resourceHandleLabel,
	resourceMarkerState,
	specItems,
	textHasResourceMarkers,
	type PromptResourceSpec
} from './promptResources';

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_videos', kind: 'video', token: '<Video @>' },
	{ field: 'reference_audio', kind: 'audio', token: '<Audio @>' }
];

describe('encodeResourceMarker / parseResourceMarker', () => {
	it('round-trips a simple field and item key', () => {
		const marker = encodeResourceMarker('references', 'storage/uploads/cat.png');
		expect(marker).toBe('@[references:storage/uploads/cat.png]');
		expect(parseResourceMarker(marker)).toEqual({ field: 'references', item_key: 'storage/uploads/cat.png' });
	});

	it('rejects malformed markers', () => {
		expect(parseResourceMarker('@[references]')).toBeNull();
		expect(parseResourceMarker('#references:cat.png')).toBeNull();
		expect(parseResourceMarker('@[1field:cat.png]')).toBeNull();
	});
});

describe('mediaItemKey', () => {
	it('prefers relative_path, then path, then url', () => {
		expect(mediaItemKey({ relative_path: 'a', path: 'b', url: 'c' })).toBe('a');
		expect(mediaItemKey({ path: 'b', url: 'c' })).toBe('b');
		expect(mediaItemKey({ url: 'c' })).toBe('c');
	});

	it('treats a string item as its own key', () => {
		expect(mediaItemKey('uploads/cat.png')).toBe('uploads/cat.png');
		expect(mediaItemKey('')).toBeNull();
	});

	it('returns null for unusable items', () => {
		expect(mediaItemKey({})).toBeNull();
		expect(mediaItemKey(null)).toBeNull();
		expect(mediaItemKey(42)).toBeNull();
	});
});

describe('mediaFieldItems / itemPosition / itemAtPosition', () => {
	it('normalizes empty, single, and array field values', () => {
		expect(mediaFieldItems(null)).toEqual([]);
		expect(mediaFieldItems('')).toEqual([]);
		expect(mediaFieldItems({ url: 'a' })).toEqual([{ url: 'a' }]);
		expect(mediaFieldItems([{ url: 'a' }, { url: 'b' }])).toEqual([{ url: 'a' }, { url: 'b' }]);
	});

	it('resolves 1-based position by item key', () => {
		const value = [{ relative_path: 'a.png' }, { relative_path: 'b.png' }, { relative_path: 'c.png' }];
		expect(itemPosition(value, 'a.png')).toBe(1);
		expect(itemPosition(value, 'c.png')).toBe(3);
		expect(itemPosition(value, 'missing.png')).toBeNull();
		expect(itemAtPosition(value, 2)).toEqual({ relative_path: 'b.png' });
	});

	it('renumbers when the field is reordered', () => {
		const reordered = [{ relative_path: 'c.png' }, { relative_path: 'a.png' }];
		expect(itemPosition(reordered, 'a.png')).toBe(2);
	});
});

describe('kindLabel / resourceHandleLabel / resourceGroupLabel', () => {
	it('capitalizes the kind for the chip handle', () => {
		expect(kindLabel('image')).toBe('Picture');
		expect(kindLabel('video')).toBe('Video');
		expect(kindLabel('audio')).toBe('Audio');
		expect(resourceHandleLabel(specs[0], 2)).toBe('Picture 2');
		expect(resourceHandleLabel(specs[1], 1)).toBe('Video 1');
	});

	it('prefers the declared group label, falling back to a pluralized kind', () => {
		expect(resourceGroupLabel(specs[0])).toBe('Pictures');
		expect(resourceGroupLabel(specs[1])).toBe('Videos');
	});
});

describe('resourceMarkerState', () => {
	it('reports position and dangling=false for a live item', () => {
		const formValues = { references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }] };
		const state = resourceMarkerState({ field: 'references', item_key: 'b.png' }, specs, formValues);
		expect(state).toEqual({ field: 'references', itemKey: 'b.png', spec: specs[0], position: 2, dangling: false });
	});

	it('is dangling when the field is unmapped', () => {
		const state = resourceMarkerState({ field: 'unknown_field', item_key: 'a.png' }, specs, {});
		expect(state.dangling).toBe(true);
		expect(state.spec).toBeNull();
	});

	it('is dangling when the item is no longer in the field', () => {
		const state = resourceMarkerState({ field: 'references', item_key: 'gone.png' }, specs, { references: [] });
		expect(state.dangling).toBe(true);
		expect(state.spec).toBe(specs[0]);
		expect(state.position).toBeNull();
	});

	it('uses the numbering override instead of the raw array index when given one', () => {
		const formValues = { references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }] };
		const numbering = { positionFor: () => 7 };
		const state = resourceMarkerState({ field: 'references', item_key: 'b.png' }, specs, formValues, numbering);
		expect(state.position).toBe(7);
		expect(state.dangling).toBe(false);
	});

	it('is dangling when the numbering override reports no position', () => {
		const formValues = { references: [{ relative_path: 'a.png' }] };
		const numbering = { positionFor: () => null };
		const state = resourceMarkerState({ field: 'references', item_key: 'a.png' }, specs, formValues, numbering);
		expect(state.position).toBeNull();
		expect(state.dangling).toBe(true);
	});
});

describe('resolveResourceMarkers', () => {
	const formValues = {
		references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }],
		reference_videos: [{ relative_path: 'clip.mp4' }]
	};

	it('replaces every marker with its token, substituting @ with the position', () => {
		const text = 'a cat next to @[references:b.png] and @[reference_videos:clip.mp4]';
		expect(resolveResourceMarkers(text, specs, formValues)).toBe(
			'a cat next to <Picture 2> and <Video 1>'
		);
	});

	it('leaves a dangling marker untouched', () => {
		const text = 'see @[references:missing.png]';
		expect(resolveResourceMarkers(text, specs, formValues)).toBe(text);
	});

	it('leaves an unmapped field marker untouched', () => {
		const text = 'see @[unmapped:a.png]';
		expect(resolveResourceMarkers(text, specs, formValues)).toBe(text);
	});

	it('is a no-op on text without markers', () => {
		expect(resolveResourceMarkers('plain text', specs, formValues)).toBe('plain text');
	});

	it('substitutes the numbering override position instead of the raw array index', () => {
		const text = '@[references:a.png] and @[references:b.png]';
		const numbering = {
			positionFor: (field: string, itemKey: string) => (field === 'references' && itemKey === 'b.png' ? 1 : null)
		};
		expect(resolveResourceMarkers(text, specs, formValues, numbering)).toBe('@[references:a.png] and <Picture 1>');
	});

	it('substitutes every @ occurrence in a token', () => {
		const multiSpec: PromptResourceSpec[] = [{ field: 'refs', kind: 'image', token: '<img@ (#@)>' }];
		const text = '@[refs:a.png]';
		expect(resolveResourceMarkers(text, multiSpec, { refs: [{ relative_path: 'a.png' }] })).toBe('<img1 (#1)>');
	});
});

describe('findResourceProblems / collectResourceProblems', () => {
	const formValues = { references: [{ relative_path: 'a.png' }] };

	it('finds no problems for a resolvable marker', () => {
		expect(findResourceProblems('@[references:a.png]', specs, formValues)).toEqual([]);
	});

	it('flags an unmapped field', () => {
		const problems = findResourceProblems('@[nope:a.png]', specs, formValues);
		expect(problems).toHaveLength(1);
		expect(problems[0].reason).toBe('unmapped');
		expect(problems[0].field).toBe('nope');
	});

	it('flags a missing item', () => {
		const problems = findResourceProblems('@[references:gone.png]', specs, formValues);
		expect(problems).toHaveLength(1);
		expect(problems[0].reason).toBe('missing');
		expect(problems[0].itemKey).toBe('gone.png');
	});

	it('collects problems across positive and negative prompt text', () => {
		const problems = collectResourceProblems(
			['@[references:a.png]', '@[references:gone.png]', null, undefined],
			specs,
			formValues
		);
		expect(problems).toHaveLength(1);
		expect(problems[0].reason).toBe('missing');
	});
});

describe('textHasResourceMarkers', () => {
	it('detects a marker anywhere in the text', () => {
		expect(textHasResourceMarkers('a @[references:a.png] cat')).toBe(true);
		expect(textHasResourceMarkers('no markers here')).toBe(false);
		expect(textHasResourceMarkers(null)).toBe(false);
		expect(textHasResourceMarkers(undefined)).toBe(false);
	});
});

describe('deriveResourcesFromText', () => {
	it('returns an empty map for text without markers', () => {
		expect(deriveResourcesFromText('plain text', {})).toEqual({});
	});

	it('preserves an existing id for a marker that is still present', () => {
		const previous = { 'res-1': { field: 'references', item_key: 'a.png' } };
		const result = deriveResourcesFromText('@[references:a.png]', previous);
		expect(result).toEqual(previous);
	});

	it('mints a fresh id for a marker with no prior entry', () => {
		const result = deriveResourcesFromText('@[references:a.png]', {});
		const ids = Object.keys(result);
		expect(ids).toHaveLength(1);
		expect(result[ids[0]]).toEqual({ field: 'references', item_key: 'a.png' });
	});

	it('drops an entry whose marker is no longer in the text', () => {
		const previous = {
			'res-1': { field: 'references', item_key: 'a.png' },
			'res-2': { field: 'references', item_key: 'b.png' }
		};
		const result = deriveResourcesFromText('@[references:a.png]', previous);
		expect(result).toEqual({ 'res-1': { field: 'references', item_key: 'a.png' } });
	});

	it('assigns a distinct id per occurrence of a duplicated marker', () => {
		const result = deriveResourcesFromText('@[references:a.png] and again @[references:a.png]', {});
		expect(Object.keys(result)).toHaveLength(2);
		for (const ref of Object.values(result)) {
			expect(ref).toEqual({ field: 'references', item_key: 'a.png' });
		}
	});
});

describe('a field that carries several kinds', () => {
	const mixedSpecs: PromptResourceSpec[] = [
		{ field: 'refs', kind: 'image', token: '<Picture @>' },
		{ field: 'refs', kind: 'video', token: '<Video @>' },
		{ field: 'refs', kind: 'audio', token: '<Audio @>' }
	];
	const refs = [
		{ relative_path: 'a.png', type: 'image' },
		{ relative_path: 'clip1.mp4', type: 'video' },
		{ relative_path: 'b.png', type: 'image' },
		{ relative_path: 'clip2.mp4', type: 'video' },
		{ relative_path: 'voice.wav', type: 'audio' }
	];
	const formValues = { refs };

	it('picks the spec by field and kind', () => {
		expect(findResourceSpec(mixedSpecs, 'refs', 'video')).toBe(mixedSpecs[1]);
		expect(findResourceSpec(mixedSpecs, 'refs', 'audio')).toBe(mixedSpecs[2]);
		expect(findResourceSpec(mixedSpecs, 'refs')).toBe(mixedSpecs[0]);
	});

	it('finds no spec for a kind the field never declared when several are declared', () => {
		expect(findResourceSpec(mixedSpecs.slice(0, 2), 'refs', 'audio')).toBeUndefined();
	});

	it('keeps the lone spec of a single-kind field whatever the item kind', () => {
		expect(findResourceSpec([specs[0]], 'references', 'video')).toBe(specs[0]);
	});

	it('counts the position within the kind', () => {
		expect(itemPosition(refs, 'b.png', 'image')).toBe(2);
		expect(itemPosition(refs, 'clip2.mp4', 'video')).toBe(2);
		expect(itemPosition(refs, 'voice.wav', 'audio')).toBe(1);
		expect(itemPosition(refs, 'clip2.mp4')).toBe(4);
	});

	it('does not find an item under the wrong kind', () => {
		expect(itemPosition(refs, 'a.png', 'video')).toBeNull();
	});

	it('resolves each marker to its own kind token and position', () => {
		const text = '@[refs:b.png] meets @[refs:clip2.mp4] over @[refs:voice.wav]';
		expect(resolveResourceMarkers(text, mixedSpecs, formValues)).toBe('<Picture 2> meets <Video 2> over <Audio 1>');
	});

	it('reports the position within the kind in the marker state', () => {
		const state = resourceMarkerState({ field: 'refs', item_key: 'clip2.mp4' }, mixedSpecs, formValues);
		expect(state).toMatchObject({ spec: mixedSpecs[1], position: 2, dangling: false });
	});

	it('is dangling when the item left the field', () => {
		const state = resourceMarkerState({ field: 'refs', item_key: 'gone.mp4' }, mixedSpecs, formValues);
		expect(state.dangling).toBe(true);
	});

	it('lists only the items of the spec kind, numbered inside the kind', () => {
		expect(specItems(mixedSpecs, mixedSpecs[0], refs).map(mediaItemKey)).toEqual(['a.png', 'b.png']);
		expect(specItems(mixedSpecs, mixedSpecs[1], refs).map(mediaItemKey)).toEqual(['clip1.mp4', 'clip2.mp4']);
		expect(specItems(mixedSpecs, mixedSpecs[2], refs)).toHaveLength(1);
	});

	it('lists every item when the field has a single spec', () => {
		expect(specItems([mixedSpecs[0]], mixedSpecs[0], refs)).toHaveLength(5);
	});

	it('keys a group by field and kind only when the field is shared', () => {
		expect(resourceGroupKey(mixedSpecs, mixedSpecs[1])).toBe('refs:video');
		expect(resourceGroupKey([mixedSpecs[0]], mixedSpecs[0])).toBe('refs');
		expect(findSpecByGroupKey(mixedSpecs, 'refs:audio')).toBe(mixedSpecs[2]);
		expect(findSpecByGroupKey(mixedSpecs, 'refs')).toBeUndefined();
	});
});
