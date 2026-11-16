import { describe, expect, it } from 'vitest';
import { railFor, railGroups, railTools, type RailGroupEntry, type RailToolEntry } from './railTools';
import { pluginOwner } from './registry';

const group = (id: string, order: number): RailGroupEntry => ({ id, editor: 'audio', label: id, order });
const tool = (id: string, groupId: string, extra: Partial<RailToolEntry> = {}): RailToolEntry => ({
	id,
	editor: 'audio',
	group: groupId,
	label: id,
	icon: 'x',
	...extra
});

describe('railFor', () => {
	it('builds groups by order and tools by order, with unordered tools last', () => {
		const disposers = [
			railGroups.register(group('late', 20)),
			railGroups.register(group('early', 10)),
			railTools.register(tool('b', 'early', { order: 20 })),
			railTools.register(tool('a', 'early', { order: 10 })),
			railTools.register(tool('free', 'early')),
			railTools.register(tool('only', 'late', { keys: 'O' }))
		];
		const rail = railFor('audio', { editor: 'audio', hasSource: true });
		expect(rail.map((entry) => entry.id)).toEqual(['early', 'late']);
		expect(rail[0].tools.map((entry) => entry.id)).toEqual(['a', 'b', 'free']);
		expect(rail[1].tools[0]).toEqual({ id: 'only', label: 'only', icon: 'x', keys: 'O' });
		disposers.forEach((dispose) => dispose());
	});

	it('appends extra tools to their group and drops groups with no tools', () => {
		const disposers = [railGroups.register(group('empty', 5)), railGroups.register(group('filled', 6)), railTools.register(tool('t', 'filled'))];
		const rail = railFor('audio', { editor: 'audio', hasSource: true }, { filled: [{ id: 'extra', label: 'Extra', icon: 'y' }], missing: [{ id: 'z', label: 'Z', icon: 'z' }] });
		expect(rail.map((entry) => entry.id)).toEqual(['filled']);
		expect(rail[0].tools.map((entry) => entry.id)).toEqual(['t', 'extra']);
		disposers.forEach((dispose) => dispose());
	});

	it('greys a tool that reports itself unavailable and survives a throwing check from a plugin', () => {
		const disposers = [
			railGroups.register(group('g', 1)),
			railTools.register(tool('off', 'g', { available: () => ({ ok: false, reason: 'Needs a selection' }) })),
			railTools.register(tool('acme.bad', 'g', { available: () => { throw new Error('boom'); } }), pluginOwner('acme'))
		];
		const rail = railFor('audio', { editor: 'audio', hasSource: true });
		const byId = Object.fromEntries(rail[0].tools.map((entry) => [entry.id, entry]));
		expect(byId.off).toMatchObject({ disabled: true, reason: 'Needs a selection' });
		expect(byId['acme.bad'].disabled).toBeUndefined();
		disposers.forEach((dispose) => dispose());
	});
});
