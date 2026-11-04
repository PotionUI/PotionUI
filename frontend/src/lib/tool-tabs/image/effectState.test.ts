import { describe, expect, it } from 'vitest';
import type { EffectTool } from '$lib/effects/catalog';
import type { FilterItem } from '$lib/filters/types';
import { canApplyEffect, canResetEffect, comparableEffect, effectDefaultsOf } from './effectState';
import type { EffectParams, LookParams, ToolEffectParams } from './steps';

const reel = { id: 'reel', name: 'Reel', intensity: 60 } as FilterItem;
const grain = {
	id: 'film_grain',
	label: 'Film grain',
	params: [{ name: 'amount', type: 'float', default: 30 }]
} as unknown as EffectTool;

const look = (intensity: number): LookParams => ({ kind: 'look', filter: 'reel', name: 'Reel', intensity });
const effect = (amount: number): ToolEffectParams => ({ kind: 'effect', tool: 'film_grain', name: 'Film grain', values: { amount } });

describe('look and effect state', () => {
	it('compares looks by filter and intensity only', () => {
		expect(comparableEffect(look(40))).toEqual({ filter: 'reel', intensity: 40 });
		expect(comparableEffect(effect(10))).toEqual({ tool: 'film_grain', amount: 10 });
	});

	it('resets a look to the intensity the look ships with', () => {
		expect(effectDefaultsOf(look(20), [reel], [])).toEqual(look(60));
		expect(effectDefaultsOf(look(20), [], [])).toEqual(look(20));
	});

	it('can reset only a value that moved from its defaults', () => {
		expect(canResetEffect(look(60), [reel], [])).toBe(false);
		expect(canResetEffect(look(20), [reel], [])).toBe(true);
		expect(canResetEffect(effect(30), [], [grain])).toBe(false);
		expect(canResetEffect(effect(5), [], [grain])).toBe(true);
		expect(canResetEffect(null, [reel], [])).toBe(false);
	});

	it('can apply a new pick, and an update only when it changed', () => {
		expect(canApplyEffect(look(60), null, false)).toBe(true);
		expect(canApplyEffect(look(60), look(60) as EffectParams, true)).toBe(false);
		expect(canApplyEffect(look(70), look(60) as EffectParams, true)).toBe(true);
		expect(canApplyEffect(null, null, false)).toBe(false);
	});
});
