import { describe, expect, it } from 'vitest';
import { buildMemoryGroups, memoryGroupTitle, notesForGroup } from './memoryGroups';
import type { MemoryNote } from '$lib/types/chat';

function note(overrides: Partial<MemoryNote>): MemoryNote {
	return {
		id: 'n1',
		user_id: 'u1',
		key: 'tone',
		content: 'Keeps things terse.',
		scope: 'global',
		scope_ref: null,
		created_at: null,
		updated_at: null,
		...overrides
	};
}

describe('buildMemoryGroups', () => {
	it('orders global, mode, preset, model and marks each available by its ref', () => {
		const groups = buildMemoryGroups({ presetId: 'p1', modelId: 'm1', modeId: 'lora-dataset' });
		expect(groups.map((g) => g.scope)).toEqual(['global', 'mode', 'preset', 'model']);
		expect(groups.every((g) => g.available)).toBe(true);
	});

	it('a mode group is available whenever a mode id resolved, independent of preset/model', () => {
		const groups = buildMemoryGroups({ presetId: null, modelId: null, modeId: 'generation' });
		const mode = groups.find((g) => g.scope === 'mode')!;
		expect(mode).toEqual({ scope: 'mode', ref: 'generation', available: true });
		expect(groups.find((g) => g.scope === 'preset')!.available).toBe(false);
		expect(groups.find((g) => g.scope === 'model')!.available).toBe(false);
	});

	it('a mode group is unavailable only when no mode id has resolved', () => {
		const groups = buildMemoryGroups({ presetId: null, modelId: null, modeId: null });
		expect(groups.find((g) => g.scope === 'mode')!.available).toBe(false);
	});
});

describe('notesForGroup', () => {
	it('matches a scoped group by scope AND scope_ref', () => {
		const notes = [
			note({ id: 'a', scope: 'mode', scope_ref: 'lora-dataset' }),
			note({ id: 'b', scope: 'mode', scope_ref: 'generation' }),
			note({ id: 'c', scope: 'preset', scope_ref: 'lora-dataset' })
		];
		const group = { scope: 'mode' as const, ref: 'lora-dataset', available: true };
		expect(notesForGroup(notes, group).map((n) => n.id)).toEqual(['a']);
	});

	it('a global group ignores scope_ref and takes every global note', () => {
		const notes = [note({ id: 'a', scope: 'global', scope_ref: null })];
		const group = { scope: 'global' as const, ref: null, available: true };
		expect(notesForGroup(notes, group).map((n) => n.id)).toEqual(['a']);
	});
});

describe('memoryGroupTitle', () => {
	const labels = { presetName: 'SDXL Portrait', modelName: 'juggernautXL', modeLabel: 'LoRA Dataset' };

	it('labels the mode group with the session mode name when known', () => {
		const group = { scope: 'mode' as const, ref: 'lora-dataset', available: true };
		expect(memoryGroupTitle(group, 2, labels)).toBe('Mode · LoRA Dataset');
	});

	it('falls back to the bare word when the mode name has not resolved', () => {
		const group = { scope: 'mode' as const, ref: 'lora-dataset', available: true };
		expect(memoryGroupTitle(group, 0, { ...labels, modeLabel: null })).toBe('Mode');
	});

	it('still labels global with its live count', () => {
		const group = { scope: 'global' as const, ref: null, available: true };
		expect(memoryGroupTitle(group, 1, labels)).toBe('Global · 1 note');
	});
});
