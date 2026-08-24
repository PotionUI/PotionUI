import { describe, expect, it } from 'vitest';
import type { ChipData, RichSegment, SavedSegment, Segment } from '$lib/types/segments';
import {
	applySegmentList,
	applyTemplateSegments,
	createBlankEditorSegment,
	effectiveSavedSegmentColor,
	flattenRichSegments,
	isPristineBlankSegment,
	removeSegmentKeepingOne,
	replaceFromSavedSegment,
	toRichSegment
} from './richSegments';

function ids(prefix = 'fresh') {
	let index = 0;
	return () => `${prefix}-${++index}`;
}

function editor(id: string, content: string, partial: Partial<Segment> = {}): Segment {
	return { id, content, type: 'content', chips: {}, enabled: true, ...partial };
}

function rich(content: string, partial: Partial<RichSegment> = {}): RichSegment {
	return { content, type: 'content', chips: {}, enabled: true, ...partial };
}

function chip(): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'palette.color',
		valueId: 'red',
		label: 'Red',
		value: 'crimson',
		allValues: [
			{
				id: 'red',
				label: 'Red',
				value: 'crimson',
				preview_file_id: 'preview-1'
			},
			{ id: 'blue', label: 'Blue', value: 'azure' }
		],
		shuffle: true,
		autoRegen: false
	};
}

describe('applySegmentList', () => {
	const target = [editor('a', 'A'), editor('b', 'B')];
	const incoming = [rich('X'), rich('Y')];

	it.each([
		['append', ['A', 'B', 'X', 'Y']],
		['prepend', ['X', 'Y', 'A', 'B']],
		['replace', ['X', 'Y']]
	] as const)('%s preserves the required order', (mode, expected) => {
		const result = applySegmentList(target, incoming, mode, ids());
		expect(result.map((segment) => segment.content)).toEqual(expected);
	});

	it('treats a pristine placeholder as an empty target for append and prepend', () => {
		const placeholder = [createBlankEditorSegment(() => 'blank')];
		expect(applySegmentList(placeholder, incoming, 'append', ids()).map((s) => s.content)).toEqual(['X', 'Y']);
		expect(applySegmentList(placeholder, incoming, 'prepend', ids()).map((s) => s.content)).toEqual(['X', 'Y']);
	});

	it('deep-copies every composition field with fresh ids and does not mutate inputs', () => {
		const source = rich('a #palette.color subject', {
			chips: { c1: chip() },
			enabled: false,
			name: 'Palette',
			color: '#123456',
			description: 'A reusable palette'
		});
		const before = structuredClone(source);
		const result = applySegmentList([], [source], 'replace', () => 'detached-1');

		expect(result[0]).toMatchObject({
			id: 'detached-1',
			content: source.content,
			enabled: false,
			isDisabled: true,
			name: 'Palette',
			color: '#123456',
			description: 'A reusable palette'
		});
		expect(result[0].chips).toEqual(source.chips);
		expect(result[0].chips).not.toBe(source.chips);
		expect(result[0].chips?.c1).not.toBe(source.chips.c1);
		expect(result[0].chips?.c1.allValues).not.toBe(source.chips.c1.allValues);

		result[0].chips!.c1.allValues[0].label = 'Changed';
		expect(source).toEqual(before);
	});

	it('keeps the one-card invariant when replacing with an empty aggregate', () => {
		const result = applySegmentList(target, [], 'replace', () => 'replacement-blank');
		expect(result).toHaveLength(1);
		expect(result[0]).toMatchObject({
			id: 'replacement-blank',
			content: '',
			type: 'content'
		});
	});

	it('can update one targeted list without changing generation configuration', () => {
		const state = {
			promptSegments: target,
			negativePromptSegments: [editor('negative', 'avoid blur')],
			preset: 'portrait-preset',
			mode: 'txt2img',
			formData: { steps: 24 },
			sessionId: 'session-1',
			backendId: 'backend-1',
			seed: 42,
			tags: ['favorite'],
			generationSettings: { batchSize: 2 }
		};

		const next = {
			...state,
			promptSegments: applySegmentList(state.promptSegments, incoming, 'replace', ids())
		};

		expect(next.promptSegments.map((segment) => segment.content)).toEqual(['X', 'Y']);
		expect({ ...next, promptSegments: state.promptSegments }).toEqual(state);
		expect(next.negativePromptSegments).toBe(state.negativePromptSegments);
		expect(next.formData).toBe(state.formData);
		expect(next.generationSettings).toBe(state.generationSettings);
	});
});

describe('applyTemplateSegments', () => {
	const target = [editor('a', 'A')];
	const template = {
		id: 'tmpl-1',
		name: 'Cinematic Base',
		segments: [rich('X', { name: 'Shot' }), rich('Y')]
	};

	it('stamps each incoming card with template provenance by slot position', () => {
		const result = applyTemplateSegments(target, template, 'append', ids());
		const applied = result.slice(1);
		expect(applied.map((s) => s.template)).toEqual([
			{ id: 'tmpl-1', name: 'Cinematic Base', slot: 'Shot', position: 0 },
			{ id: 'tmpl-1', name: 'Cinematic Base', slot: 'Segment 2', position: 1 }
		]);
	});

	it('does not stamp pre-existing cards in the target list', () => {
		const result = applyTemplateSegments(target, template, 'append', ids());
		expect(result[0].template).toBeUndefined();
	});

	it('preserves append/prepend/replace ordering like applySegmentList', () => {
		expect(applyTemplateSegments(target, template, 'prepend', ids()).map((s) => s.content)).toEqual([
			'X',
			'Y',
			'A'
		]);
		expect(applyTemplateSegments(target, template, 'replace', ids()).map((s) => s.content)).toEqual([
			'X',
			'Y'
		]);
	});
});

describe('Saved Segment replacement', () => {
	const saved: SavedSegment = {
		id: 'saved-1',
		name: 'Lighting',
		category_id: 'category-1',
		tags: ['light'],
		type: 'content',
		content: 'soft light',
		chips: {},
		enabled: true,
		description: 'Soft studio lighting'
	};

	it('uses the Saved Segment color before the category color', () => {
		expect(effectiveSavedSegmentColor({ ...saved, color: '#111111' }, { color: '#222222' })).toBe('#111111');
	});

	it('copies the category color when the Saved Segment has no override', () => {
		expect(effectiveSavedSegmentColor(saved, { color: '#222222' })).toBe('#222222');
	});

	it('replaces exactly one card and copies metadata with a fresh id', () => {
		const target = [editor('one', 'A'), editor('two', 'B'), editor('three', 'C')];
		const result = replaceFromSavedSegment(target, 'two', saved, { color: '#abcdef' }, () => 'saved-copy');

		expect(result.map((segment) => segment.content)).toEqual(['A', 'soft light', 'C']);
		expect(result[1]).toMatchObject({
			id: 'saved-copy',
			name: 'Lighting',
			color: '#abcdef',
			description: 'Soft studio lighting'
		});
		expect(target[1]).toEqual(editor('two', 'B'));
	});

	it('clears template provenance when replacing a slot-derived card', () => {
		const target = [
			editor('two', 'X', {
				template: { id: 'tmpl-1', name: 'Cinematic Base', slot: 'Shot', position: 0 }
			})
		];
		const result = replaceFromSavedSegment(target, 'two', saved, null, () => 'saved-copy');
		expect(result[0].template).toBeUndefined();
	});
});

describe('list invariants and flattening', () => {
	it('recognizes only an untouched content card as pristine', () => {
		const blank = createBlankEditorSegment(() => 'blank');
		expect(isPristineBlankSegment(blank)).toBe(true);
		expect(isPristineBlankSegment({ ...blank, name: 'Empty slot' })).toBe(false);
		expect(isPristineBlankSegment({ ...blank, type: 'break' })).toBe(false);
		expect(isPristineBlankSegment({ ...blank, enabled: false, isDisabled: true })).toBe(false);
	});

	it('prevents deleting the final card', () => {
		const only = [editor('only', 'A')];
		expect(removeSegmentKeepingOne(only, 'only')).toEqual(only);
		expect(removeSegmentKeepingOne([...only, editor('two', 'B')], 'only').map((s) => s.id)).toEqual(['two']);
	});

	it('flattens enabled content, breaks, and chip values while ignoring collapsed UI state', () => {
		expect(
			flattenRichSegments([
				editor('one', 'portrait', { isCollapsed: true }),
				editor('disabled', 'hidden', { enabled: false, isDisabled: true }),
				editor('break', '', { type: 'break' }),
				editor('chip', '#palette.color light', { chips: { c1: chip() } })
			])
		).toBe('portrait BREAK crimson light');
	});

	it('defaults to a comma join between enabled content segments', () => {
		expect(flattenRichSegments([editor('one', 'Verse one'), editor('two', 'Chorus one')])).toBe(
			'Verse one, Chorus one'
		);
	});

	it('paragraph join separates enabled content segments with a blank line, chips resolved and internal newlines preserved', () => {
		expect(
			flattenRichSegments(
				[
					editor('verse', '[Verse]\nrain on the window'),
					editor('disabled', 'hidden', { enabled: false, isDisabled: true }),
					editor('blank', '   '),
					editor('chorus', '[Chorus]\n#palette.color light', { chips: { c1: chip() } })
				],
				'paragraph'
			)
		).toBe('[Verse]\nrain on the window\n\n[Chorus]\ncrimson light');
	});

	it('strips editor, AI, and source state from persistent copies', () => {
		const persistent = toRichSegment({
			...editor('editor-id', 'content', {
				title: 'Legacy name',
				isCollapsed: true
			}),
			isAIGenerated: true,
			sourcePromptId: 'source-id'
		} as Segment & { isAIGenerated: boolean; sourcePromptId: string });
		expect(persistent).toEqual({
			type: 'content',
			content: 'content',
			chips: {},
			enabled: true,
			name: 'Legacy name'
		});
		expect(persistent).not.toHaveProperty('id');
		expect(persistent).not.toHaveProperty('isCollapsed');
		expect(persistent).not.toHaveProperty('sourcePromptId');
	});
});
