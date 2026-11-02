import { describe, expect, it } from 'vitest';
import { apply, appliedSteps, beginEdit, createStack, effectiveSteps, toggleHidden, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { effectTools } from '$lib/effects/catalog';
import { EFFECT_CATALOG } from '../../../../tests/component/fixtures/effectCatalog';
import {
	compileSteps,
	defaultParams,
	draftFor,
	hasPixelEffects,
	isNoop,
	isVideoOp,
	playbackPreview,
	railToolOf,
	summarize,
	type VideoOp,
	type VideoParams
} from './videoSteps';

const clip = { width: 1920, height: 1080, duration: 8 };
const effects = new Map(effectTools(EFFECT_CATALOG).map((effect) => [effect.id, effect]));

function stackOf(...entries: Array<[VideoOp, VideoParams]>): EditStackState<VideoParams> {
	let state = createStack<VideoParams>();
	for (const [op, params] of entries) state = apply(beginEdit(state, draftFor(op, params, effects)));
	return state;
}

describe('the stack as a runs payload', () => {
	it('compiles each visible step in order with the catalog params', () => {
		const state = stackOf(
			['trim', { start: 1.2, end: 6.4 }],
			['volume', { unit: 'percent', percent: 80, db: 0, fade_in: 0, fade_out: 0.5 }],
			['film_grain', { amount: 30, size: 2, color: false, seed: 42 }]
		);
		expect(compileSteps(effectiveSteps(state), clip, { effects }).steps).toEqual([
			{ tool: 'trim', params: { start: 1.2, end: 6.4 } },
			{ tool: 'volume', params: { unit: 'percent', percent: 80, fade_in: 0, fade_out: 0.5 } },
			{ tool: 'film_grain', params: { amount: 30, size: 2, color: false, seed: 42 } }
		]);
	});

	it('compiles any catalog effect generically, filling defaults and typing values from its params', () => {
		const state = stackOf(['retro-tape.scanlines', { strength: '60', gap: 4.7 }], ['film_grain', { amount: 45 }]);
		expect(compileSteps(effectiveSteps(state), clip, { effects }).steps).toEqual([
			{ tool: 'retro-tape.scanlines', params: { strength: 60, gap: 4, roll: 8 } },
			{ tool: 'film_grain', params: { amount: 45, size: 1.5, color: false, seed: 0 } }
		]);
		expect(compileSteps(effectiveSteps(state), clip).steps[0]).toEqual({ tool: 'retro-tape.scanlines', params: { strength: '60', gap: 4.7 } });
	});

	it('skips hidden steps', () => {
		let state = stackOf(['trim', { start: 1, end: 5 }], ['mute', {}], ['fix_colors', { white_balance: true, contrast: false, strength: 60 }]);
		state = toggleHidden(state, state.steps[1].id);
		const tools = compileSteps(appliedSteps(state), clip).steps.map((step) => step.tool);
		expect(tools).toEqual(['trim', 'fix_colors']);
	});

	it('writes later trims and the split point in the time of their place in the chain', () => {
		const state = stackOf(['speed', { speed: 2, keep_pitch: true }], ['trim', { start: 2, end: 6 }], ['split', { at: 4 }]);
		const { steps, chain } = compileSteps(effectiveSteps(state), clip);
		expect(steps).toEqual([
			{ tool: 'speed', params: { speed: 2, keep_pitch: true } },
			{ tool: 'trim', params: { start: 1, end: 3 } },
			{ tool: 'split', params: { mode: 'at', at: 1 } }
		]);
		expect(chain.duration).toBeCloseTo(2, 6);
		expect(compileSteps(effectiveSteps(state), clip, { skipSplit: true }).steps.map((s) => s.tool)).toEqual(['speed', 'trim']);
	});

	it('turns a crop with rotate and flip into rotate, flip and a pixel crop of the turned frame', () => {
		const state = stackOf(['crop', { rect: { x: 0.25, y: 0, width: 0.5, height: 0.5 }, rotation: 90, flipH: true, flipV: false }], ['resize', { short: 360, quality: 'small' }]);
		const { steps, chain } = compileSteps(effectiveSteps(state), clip);
		expect(steps).toEqual([
			{ tool: 'rotate', params: { degrees: 90 } },
			{ tool: 'flip', params: { axis: 'horizontal' } },
			{ tool: 'crop', params: { x: 270, y: 0, width: 540, height: 960 } },
			{ tool: 'resize', params: { width: 360, quality: 'small' } }
		]);
		expect(chain.size).toEqual({ width: 360, height: 640 });
	});

	it('sends the full params of the audio, colour and split steps', () => {
		const state = stackOf(
			['volume', { unit: 'db', percent: 100, db: -6, fade_in: 0.5, fade_out: 0 }],
			['replace_audio', { audio: { item_id: 'song-1' }, name: 'song.mp3', start: 2, loop: false }],
			['mute', {}],
			['fix_colors', { white_balance: false, contrast: true, strength: 60 }],
			['split', { at: 3 }]
		);
		expect(compileSteps(effectiveSteps(state), clip, { effects }).steps).toEqual([
			{ tool: 'volume', params: { unit: 'db', db: -6, fade_in: 0.5, fade_out: 0 } },
			{ tool: 'replace_audio', params: { audio: { item_id: 'song-1' }, start: 2, loop: false } },
			{ tool: 'mute', params: {} },
			{ tool: 'fix_colors', params: { white_balance: false, contrast: true, strength: 60 } },
			{ tool: 'split', params: { mode: 'at', at: 3 } }
		]);
		expect(compileSteps(effectiveSteps(state), clip, { skipSplit: true }).steps.map((step) => step.tool)).not.toContain('split');
	});

	it('leaves out a replace step with no track and a split outside the clip', () => {
		const state = stackOf(['replace_audio', { audio: null, name: '', start: 0, loop: true }], ['split', { at: 9 }]);
		expect(compileSteps(effectiveSteps(state), clip).steps).toEqual([]);
	});
});

describe('playback preview', () => {
	it('folds speed, volume and mute into what the media element plays', () => {
		const state = stackOf(
			['trim', { start: 1, end: 7 }],
			['speed', { speed: 1.5, keep_pitch: true }],
			['volume', { unit: 'db', percent: 100, db: -6, fade_in: 0, fade_out: 0 }],
			['crop', { rect: { x: 0, y: 0, width: 0.5, height: 1 }, rotation: 0, flipH: false, flipV: false }]
		);
		const preview = playbackPreview(effectiveSteps(state), clip);
		expect(preview.rate).toBeCloseTo(1.5, 6);
		expect(preview.duration).toBeCloseTo(4, 6);
		expect(preview.gain).toBeCloseTo(0.501, 2);
		expect(preview.muted).toBe(false);
		expect(preview.crop?.rect.width).toBe(0.5);
		expect(preview.size).toEqual({ width: 960, height: 1080 });
		expect(playbackPreview(effectiveSteps(stackOf(['mute', {}])), clip).muted).toBe(true);
	});
});

describe('step helpers', () => {
	it('names the rail tool of an op, with every effect under Effects', () => {
		expect(railToolOf('film_grain')).toBe('effects');
		expect(railToolOf('retro-tape.scanlines')).toBe('effects');
		expect(isVideoOp('retro-tape.scanlines')).toBe(true);
		expect(isVideoOp('rotate')).toBe(false);
		expect(railToolOf('replace_audio')).toBe('replace');
		expect(railToolOf('trim')).toBe('trim');
		expect(railToolOf('rotate')).toBeNull();
	});

	it('summarises steps in the stack wording', () => {
		expect(summarize('trim', { start: 1.2, end: 6.4 })).toBe('Keep 1.20 → 6.40 s');
		expect(summarize('volume', { unit: 'percent', percent: 80, fade_out: 0.5 })).toBe('80% · fade out 0.5 s');
		expect(summarize('film_grain', { amount: 30, size: 2, color: true }, effects)).toBe('Amount 30% · Size 2 · colour grain');
		expect(summarize('retro-tape.scanlines', {}, effects)).toBe('Strength 35% · Line gap 3px · Roll speed 8');
	});

	it('labels effect steps and fills their defaults from the catalog', () => {
		const draft = draftFor('retro-tape.scanlines', {}, effects);
		expect([draft.label, draft.icon, draft.lane]).toEqual(['Scanlines', 'editor-effects', undefined]);
		expect(draftFor('gone.effect', {}).label).toBe('Effect');
		expect(defaultParams('fix_colors', clip, 0, effects)).toEqual({ white_balance: true, contrast: true, strength: 100 });
	});

	it('knows a full-clip trim and a 1× speed do nothing', () => {
		const kept = { start: 0, end: 8 };
		expect(isNoop('trim', { start: 0, end: 8 }, clip, kept)).toBe(true);
		expect(isNoop('trim', { start: 1, end: 8 }, clip, kept)).toBe(false);
		expect(isNoop('speed', { speed: 1 }, clip, kept)).toBe(true);
		expect(isNoop('mute', {}, clip, kept)).toBe(false);
	});

	it('spots pixel effects in a payload', () => {
		expect(hasPixelEffects([{ tool: 'trim', params: {} }])).toBe(false);
		expect(hasPixelEffects([{ tool: 'rotate', params: {} }, { tool: 'mute', params: {} }])).toBe(false);
		expect(hasPixelEffects([{ tool: 'film_grain', params: {} }])).toBe(true);
		expect(hasPixelEffects([{ tool: 'retro-tape.scanlines', params: {} }])).toBe(true);
	});
});
