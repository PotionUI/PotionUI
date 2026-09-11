import { describe, expect, it } from 'vitest';
import type { PresetStyle } from '$lib/types/api';
import type { Segment } from '$lib/types/segments';
import {
	appliedStyleTag,
	applyStyleToPrompt,
	clearStyle,
	isStyleApplied,
	parseStyleSegmentId,
	styleSegmentId
} from './styleSegments';

function editor(id: string, content: string, partial: Partial<Segment> = {}): Segment {
	return { id, content, type: 'content', chips: {}, enabled: true, ...partial };
}

function style(id: string, partial: Partial<PresetStyle> = {}): PresetStyle {
	return {
		id,
		name: `Style ${id}`,
		category: 'General',
		prepend: `${id} prepend`,
		append: `${id} append`,
		example_prompt: `${id} example`,
		...partial
	};
}

describe('styleSegmentId / parseStyleSegmentId', () => {
	it('round-trips through a deterministic id', () => {
		const id = styleSegmentId('preset-1', 'retro-90s', 'prepend');
		expect(parseStyleSegmentId(id)).toEqual({ presetId: 'preset-1', styleId: 'retro-90s', part: 'prepend' });
	});

	it('rejects ids that are not the style tag shape', () => {
		expect(parseStyleSegmentId('fresh-1')).toBeNull();
		expect(parseStyleSegmentId('style:only-two-parts')).toBeNull();
		expect(parseStyleSegmentId('style:preset:style:not-a-part')).toBeNull();
	});
});

describe('applyStyleToPrompt', () => {
	it('wraps an empty prompt with just the two cards', () => {
		const result = applyStyleToPrompt([], [], 'preset-1', style('retro'));
		expect(result.promptSegments.map((s) => s.content)).toEqual(['retro prepend', 'retro append']);
		expect(result.promptSegments[0].id).toBe(styleSegmentId('preset-1', 'retro', 'prepend'));
		expect(result.promptSegments[1].id).toBe(styleSegmentId('preset-1', 'retro', 'append'));
	});

	it('names the prepend/append cards distinctly so they never read as duplicates', () => {
		const result = applyStyleToPrompt([], [], 'preset-1', style('retro'));
		expect(result.promptSegments.map((s) => s.name)).toEqual(['Style retro · start', 'Style retro · end']);
	});

	it('drops a pristine placeholder segment rather than keeping it between the pair', () => {
		const placeholder = [editor('blank-1', '')];
		const result = applyStyleToPrompt(placeholder, [], 'preset-1', style('retro'));
		expect(result.promptSegments).toHaveLength(2);
	});

	it('preserves user segments between the prepend and append cards', () => {
		const user = [editor('u1', 'a cat'), editor('u2', 'wearing a hat')];
		const result = applyStyleToPrompt(user, [], 'preset-1', style('retro'));
		expect(result.promptSegments.map((s) => s.id)).toEqual([
			styleSegmentId('preset-1', 'retro', 'prepend'),
			'u1',
			'u2',
			styleSegmentId('preset-1', 'retro', 'append')
		]);
	});

	it('appends a negative card when the style declares one', () => {
		const result = applyStyleToPrompt([], [editor('n1', 'blurry')], 'preset-1', style('retro', { negative: 'oversaturated' }));
		expect(result.negativeSegments.map((s) => s.content)).toEqual(['blurry', 'oversaturated']);
		expect(result.negativeSegments[1].id).toBe(styleSegmentId('preset-1', 'retro', 'negative'));
		expect(result.negativeSegments[1].name).toBe('Style retro · negative');
	});

	it('trims the separator the joiner would otherwise double up on the seam', () => {
		const result = applyStyleToPrompt(
			[],
			[],
			'preset-1',
			style('retro', { prepend: 'old, anime screenshot, ', append: ', 1990s (style), retro' })
		);
		expect(result.promptSegments.map((s) => s.content)).toEqual(['old, anime screenshot', '1990s (style), retro']);
	});

	it('trims both ends of the negative card', () => {
		const result = applyStyleToPrompt([], [], 'preset-1', style('retro', { negative: ', oversaturated, ' }));
		expect(result.negativeSegments[0].content).toBe('oversaturated');
	});

	it('leaves already-clean prepend/append/negative content untouched', () => {
		const result = applyStyleToPrompt([], [], 'preset-1', style('retro', { negative: 'oversaturated' }));
		expect(result.promptSegments.map((s) => s.content)).toEqual(['retro prepend', 'retro append']);
		expect(result.negativeSegments[0].content).toBe('oversaturated');
	});

	it('leaves the negative list untouched when the style has no negative', () => {
		const result = applyStyleToPrompt([], [editor('n1', 'blurry')], 'preset-1', style('retro'));
		expect(result.negativeSegments.map((s) => s.content)).toEqual(['blurry']);
	});

	it('replaces a previously applied style pair at the same positions', () => {
		const first = applyStyleToPrompt([editor('u1', 'a cat')], [], 'preset-1', style('retro'));
		const second = applyStyleToPrompt(first.promptSegments, [], 'preset-1', style('noir'));
		expect(second.promptSegments.map((s) => s.id)).toEqual([
			styleSegmentId('preset-1', 'noir', 'prepend'),
			'u1',
			styleSegmentId('preset-1', 'noir', 'append')
		]);
	});

	it('replaces the previous negative card and drops it entirely when the new style has none', () => {
		const withNegative = applyStyleToPrompt([], [editor('n1', 'blurry')], 'preset-1', style('retro', { negative: 'grainy' }));
		const replaced = applyStyleToPrompt(withNegative.promptSegments, withNegative.negativeSegments, 'preset-1', style('noir'));
		expect(replaced.negativeSegments.map((s) => s.content)).toEqual(['blurry']);
	});

	it('replaces a style applied from a different preset', () => {
		const first = applyStyleToPrompt([], [], 'preset-1', style('retro'));
		const second = applyStyleToPrompt(first.promptSegments, [], 'preset-2', style('noir'));
		expect(appliedStyleTag(second.promptSegments)).toEqual({ presetId: 'preset-2', styleId: 'noir' });
	});
});

describe('appliedStyleTag / isStyleApplied', () => {
	it('is null with no style applied', () => {
		expect(appliedStyleTag([editor('u1', 'a cat')])).toBeNull();
	});

	it('resolves the applied style once both tagged cards are present', () => {
		const applied = applyStyleToPrompt([], [], 'preset-1', style('retro'));
		expect(appliedStyleTag(applied.promptSegments)).toEqual({ presetId: 'preset-1', styleId: 'retro' });
		expect(isStyleApplied(applied.promptSegments, 'preset-1', 'retro')).toBe(true);
		expect(isStyleApplied(applied.promptSegments, 'preset-1', 'noir')).toBe(false);
	});

	it('clears once either tagged card is deleted', () => {
		const applied = applyStyleToPrompt([editor('u1', 'a cat')], [], 'preset-1', style('retro'));
		const withoutPrepend = applied.promptSegments.filter((s) => s.id !== styleSegmentId('preset-1', 'retro', 'prepend'));
		expect(appliedStyleTag(withoutPrepend)).toBeNull();

		const withoutAppend = applied.promptSegments.filter((s) => s.id !== styleSegmentId('preset-1', 'retro', 'append'));
		expect(appliedStyleTag(withoutAppend)).toBeNull();
	});
});

describe('clearStyle', () => {
	it('removes the tagged prepend/append cards, keeping user segments', () => {
		const applied = applyStyleToPrompt([editor('u1', 'a cat')], [], 'preset-1', style('retro'));
		const cleared = clearStyle(applied.promptSegments, applied.negativeSegments);
		expect(cleared.promptSegments.map((s) => s.id)).toEqual(['u1']);
		expect(appliedStyleTag(cleared.promptSegments)).toBeNull();
	});

	it('removes the tagged negative card, keeping user negative segments', () => {
		const applied = applyStyleToPrompt(
			[],
			[editor('n1', 'blurry')],
			'preset-1',
			style('retro', { negative: 'oversaturated' })
		);
		const cleared = clearStyle(applied.promptSegments, applied.negativeSegments);
		expect(cleared.negativeSegments.map((s) => s.id)).toEqual(['n1']);
	});

	it('is a no-op on segments with no style applied', () => {
		const positive = [editor('u1', 'a cat')];
		const negative = [editor('n1', 'blurry')];
		const cleared = clearStyle(positive, negative);
		expect(cleared.promptSegments.map((s) => s.id)).toEqual(['u1']);
		expect(cleared.negativeSegments.map((s) => s.id)).toEqual(['n1']);
	});
});
