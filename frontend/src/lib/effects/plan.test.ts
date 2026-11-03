import { describe, expect, it } from 'vitest';
import { EFFECT_CATALOG, LOOK_TOOL } from '../../../tests/component/fixtures/effectCatalog';
import { effectGroups, effectTools, type EffectTool } from './catalog';
import { frameTime } from './ShaderChain';
import { analysisKey, compose, planChain, rotation, type Affine, type EffectPass, type GeometryPass } from './plan';
import type { MediaToolStep } from '$lib/media-tools/types';

const effects = new Map<string, EffectTool>(effectTools(EFFECT_CATALOG).map((effect) => [effect.id, effect]));
const source = { width: 1920, height: 1080, duration: 8, fps: 24 };

function plan(steps: MediaToolStep[], extra: Partial<Parameters<typeof planChain>[0]> = {}) {
	return planChain({ steps, source, effects, cap: 1920, ...extra });
}

function apply(map: Affine, u: number, v: number): [number, number] {
	return [map[0] * u + map[1] * v + map[2], map[3] * u + map[4] * v + map[5]];
}

const effectPasses = (steps: MediaToolStep[], extra = {}) => plan(steps, extra).passes.filter((pass): pass is EffectPass => pass.kind === 'effect');

describe('the effect catalog', () => {
	it('takes every video effect from the catalog and leaves out edits and audio tools', () => {
		expect([...effects.keys()].sort()).toEqual(['film_grain', 'fix_colors', 'retro-tape.scanlines', 'retro-tape.wobble']);
	});

	it('groups effects by their group, sorted by label, with unknown groups under More', () => {
		const groups = effectGroups([...effects.values()]);
		expect(groups.map((group) => group.label)).toEqual(['Colour', 'Texture', 'More']);
		expect(groups[1].effects.map((effect) => effect.label)).toEqual(['Film grain', 'Scanlines']);
		expect(groups[2].effects.map((effect) => effect.id)).toEqual(['retro-tape.wobble']);
	});
});

describe('the pass chain', () => {
	it('runs a source pass, then one pass per effect in stack order', () => {
		const result = plan([
			{ tool: 'retro-tape.scanlines', params: { strength: 50 } },
			{ tool: 'film_grain', params: { amount: 60, size: 2, color: true, seed: 7 } }
		]);
		expect(result.passes.map((pass) => (pass.kind === 'effect' ? pass.tool : pass.kind))).toEqual(['geometry', 'retro-tape.scanlines', 'film_grain']);
		expect(result.effects).toBe(2);
		expect(result.skipped).toEqual([]);
	});

	it('turns params and derive values into uniforms at the preview size', () => {
		const [grain] = effectPasses([{ tool: 'film_grain', params: { amount: 60, size: 2, color: true, seed: 7 } }], { cap: 960 });
		expect(grain.size).toEqual({ width: 960, height: 540 });
		expect(grain.values).toMatchObject({ amount: 60, size: 2, color: true, seed: 7, cell: 1 });
		expect(grain.values.strength).toBeCloseTo(21.42, 6);
		expect(grain.uniforms.map((uniform) => uniform.name)).toContain('strength');
	});

	it('gives each pass the time ops before it, so time is the time at its place in the stack', () => {
		const passes = effectPasses([
			{ tool: 'film_grain', params: {} },
			{ tool: 'trim', params: { start: 2, end: 6 } },
			{ tool: 'speed', params: { speed: 2 } },
			{ tool: 'retro-tape.scanlines', params: {} }
		]);
		expect(passes[0].timeOps).toEqual([]);
		expect(passes[1].timeOps).toEqual([
			{ kind: 'trim', start: 2, end: 6 },
			{ kind: 'speed', speed: 2 }
		]);
		expect(passes[0].duration).toBe(8);
		expect(passes[1].duration).toBeCloseTo(2, 9);
		const frame = { mediaTime: 3, sourceDuration: 8, fps: 24 };
		expect(frameTime(passes[0], frame)).toEqual({ time: 3, duration: 8, index: 72 });
		expect(frameTime(passes[1], frame)).toEqual({ time: 0.5, duration: 2, index: 12 });
	});

	it('folds rotate, flip and crop into one geometry pass that maps output to source', () => {
		const result = plan([
			{ tool: 'rotate', params: { degrees: 90 } },
			{ tool: 'flip', params: { axis: 'horizontal' } },
			{ tool: 'crop', params: { x: 270, y: 0, width: 540, height: 960 } },
			{ tool: 'film_grain', params: {} }
		]);
		const geometry = result.passes[0] as GeometryPass;
		expect(result.passes).toHaveLength(2);
		expect(geometry.size).toEqual({ width: 540, height: 960 });
		const [u, v] = apply(geometry.map, 0, 0);
		expect(u).toBeCloseTo(0, 9);
		expect(v).toBeCloseTo(0.25, 9);
		expect(apply(rotation(90), 0, 0).map((value) => value + 0)).toEqual([0, 1]);
		expect(apply(rotation(270), 0, 0).map((value) => value + 0)).toEqual([1, 0]);
		expect(compose(rotation(90), rotation(270)).map((value) => value + 0)).toEqual([1, 0, 0, 0, 1, 0]);
	});

	it('starts a new geometry pass when geometry comes after an effect', () => {
		const result = plan([
			{ tool: 'film_grain', params: {} },
			{ tool: 'resize', params: { height: 720 } }
		]);
		expect(result.passes.map((pass) => pass.kind)).toEqual(['geometry', 'effect', 'geometry']);
		expect(result.size).toEqual({ width: 1280, height: 720 });
		expect((result.passes[1] as EffectPass).size).toEqual({ width: 1920, height: 1080 });
	});

	it('leaves out hidden steps and skips effects with no shader or a failed one', () => {
		const result = plan(
			[
				{ tool: 'film_grain', params: {}, hidden: true },
				{ tool: 'retro-tape.wobble', params: {} },
				{ tool: 'retro-tape.scanlines', params: {} }
			],
			{ unavailable: new Set(['retro-tape.scanlines']) }
		);
		expect(result.passes.map((pass) => pass.kind)).toEqual(['geometry']);
		expect(result.effects).toBe(2);
		expect(result.skipped.map((entry) => [entry.label, entry.reason])).toEqual([
			['Tape wobble', 'no-shader'],
			['Scanlines', 'failed']
		]);
	});

	it('keeps a measured effect as identity until its analysis arrives', () => {
		const steps: MediaToolStep[] = [
			{ tool: 'trim', params: { start: 1, end: 5 } },
			{ tool: 'fix_colors', params: { strength: 40 } }
		];
		const waiting = plan(steps);
		const [pending] = waiting.passes.filter((pass): pass is EffectPass => pass.kind === 'effect');
		expect(pending.identity).toBe(true);
		expect(waiting.measuring).toEqual(['colour']);
		expect(waiting.analysis).toEqual([{ index: 1, tool: 'fix_colors', key: analysisKey(steps, 1, ['white_balance', 'contrast']), steps }]);

		const measured = { gain_r: 1.12, gain_g: 0.97, gain_b: 0.91, black: 0.04, white: 0.93 };
		const [ready] = effectPasses(steps, { analysis: (key: string) => (key === analysisKey(steps, 1, ['white_balance', 'contrast']) ? measured : null) });
		expect(ready.identity).toBe(false);
		const stale = plan([steps[0], { tool: 'fix_colors', params: { strength: 80 } }], { stale: (tool: string) => (tool === 'fix_colors' ? measured : null) });
		expect(stale.analysis).toHaveLength(1);
		expect(stale.measuring).toEqual([]);
		expect((stale.passes[1] as EffectPass).identity).toBe(false);
		expect(effects.get('fix_colors')!.block.analysisKeys).toEqual(['white_balance', 'contrast']);
		expect(stale.analysis[0].key).toBe(analysisKey(steps, 1, ['white_balance', 'contrast']));
		expect(analysisKey(steps, 1, ['white_balance'])).toBe(analysisKey([steps[0], { tool: 'fix_colors', params: { strength: 1 } }], 1, ['white_balance']));
		expect(ready.values.r).toBeCloseTo(1.048, 9);
		expect(ready.values.hi).toBeCloseTo(0.972, 9);
		expect(ready.uniforms.map((uniform) => uniform.name)).toEqual(expect.arrayContaining(['white_balance', 'strength', 'r', 'lo', 'gain_r']));
	});
});

describe('a look', () => {
	const looks = new Map<string, EffectTool>(effectTools([LOOK_TOOL]).map((effect) => [effect.id, effect]));
	const lookPlan = (params: Record<string, unknown>) => plan([{ tool: 'look', params }], { effects: looks });

	it('reads the chosen look from its option and keys its table by look and revision', () => {
		const [pass] = lookPlan({ look: 'mine:reel', strength: 0.5 }).passes.filter((item): item is EffectPass => item.kind === 'effect');
		expect(pass.textures).toEqual([{ uniform: 'table', ref: 'look:mine:reel@r7', source: 'look', value: 'mine:reel' }]);
		expect(pass.values.amount).toBeCloseTo(0.4, 9);
		expect([pass.values.s0_kind, pass.values.s0_a, pass.values.s0_b, pass.values.s0_c]).toEqual([1, 40, 50, 60]);
		expect(pass.values.plain).toBe(0);
	});

	it('plans no grade and no table for no look', () => {
		const [pass] = lookPlan({ look: '', strength: 1 }).passes.filter((item): item is EffectPass => item.kind === 'effect');
		expect(pass.values.amount).toBe(0);
		expect(pass.textures[0].ref).toBe('');
	});

	it('leaves a look the shader cannot draw, or one that is gone, to the server preview', () => {
		expect(lookPlan({ look: 'smear' }).skipped).toEqual([{ index: 0, tool: 'look', label: 'Look', reason: 'no-shader' }]);
		expect(lookPlan({ look: 'deleted' }).skipped).toEqual([{ index: 0, tool: 'look', label: 'Look', reason: 'error' }]);
	});

	it('lists image effects for the Image editor and video effects for the Video editor', () => {
		const catalog = [...EFFECT_CATALOG, LOOK_TOOL];
		expect(effectTools(catalog, 'image').map((effect) => effect.id).sort()).toEqual(['film_grain', 'look', 'retro-tape.scanlines']);
		expect(effectTools(catalog).map((effect) => effect.id)).toContain('look');
	});
});
