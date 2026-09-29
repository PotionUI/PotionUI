import { describe, it, expect } from 'vitest';
import type { Session, SessionVersionSummary } from '$lib/types/api';
import {
	filterSessions,
	splitPinned,
	applyPin,
	groupSessions,
	groupVersionsByDay,
	versionHeadline,
	versionChanges,
	humanizeKey,
	discardConsequence
} from './sessionDrawerModel';

const NOW = new Date(2026, 8, 29, 15, 0, 0);

function at(daysAgo: number, hour = 10): string {
	return new Date(2026, 8, 29 - daysAgo, hour, 0, 0).toISOString();
}

function session(id: string, name: string, daysAgo: number): Session {
	return {
		id,
		preset_id: 'p',
		name,
		data: {},
		created_at: at(daysAgo),
		updated_at: at(daysAgo)
	};
}

function version(n: number, daysAgo: number, extra: Partial<SessionVersionSummary> = {}): SessionVersionSummary {
	return { version_number: n, created_at: at(daysAgo), summary: 'Anima', ...extra };
}

describe('filterSessions', () => {
	const all = [session('a', 'Neon alley', 0), session('b', 'Forest fog', 1), session('c', 'Neon market', 2)];

	it('matches names case-insensitively', () => {
		expect(filterSessions(all, 'NEON').map((s) => s.id)).toEqual(['a', 'c']);
	});

	it('leaves the excluded current session out', () => {
		expect(filterSessions(all, '', 'a').map((s) => s.id)).toEqual(['b', 'c']);
	});

	it('returns everything for a blank query', () => {
		expect(filterSessions(all, '   ')).toHaveLength(3);
	});
});

describe('groupSessions', () => {
	it('buckets by recency, newest first, skipping empty buckets', () => {
		const groups = groupSessions(
			[
				session('old', 'Old', 60),
				session('today', 'Today', 0),
				session('week', 'Week', 4),
				session('yest', 'Yest', 1),
				session('month', 'Month', 20)
			],
			NOW
		);
		expect(groups.map((g) => [g.label, g.items.map((s) => s.id)])).toEqual([
			['Today', ['today']],
			['Yesterday', ['yest']],
			['This week', ['week']],
			['This month', ['month']],
			['Earlier', ['old']]
		]);
	});

	it('sorts inside a bucket by updated_at, newest first', () => {
		const early = { ...session('early', 'Early', 0), updated_at: at(0, 8) };
		const late = { ...session('late', 'Late', 0), updated_at: at(0, 14) };
		expect(groupSessions([early, late], NOW)[0].items.map((s) => s.id)).toEqual(['late', 'early']);
	});
});

describe('version rows', () => {
	it('groups versions by calendar day in order', () => {
		const groups = groupVersionsByDay([version(4, 0), version(3, 0), version(2, 1), version(1, 5)], NOW);
		expect(groups.map((g) => [g.label === 'Today' || g.label === 'Yesterday' ? g.label : 'date', g.items.length])).toEqual([
			['Today', 2],
			['Yesterday', 1],
			['date', 1]
		]);
	});

	it('prefers the prompt preview and falls back to the summary', () => {
		expect(versionHeadline(version(1, 0, { prompt_preview: 'neon alley at night' }))).toBe('neon alley at night');
		expect(versionHeadline(version(1, 0))).toBe('Anima');
		expect(versionHeadline(version(1, 0, { prompt_preview: '  ' }))).toBe('Anima');
	});

	it('lists fixed labels first, then changed fields through the form labels', () => {
		const summary = versionChanges(
			version(2, 0, { changes: ['prompt'], changed_fields: ['diffusion_model', 'steps'] }),
			{ diffusion_model: 'Checkpoint' }
		);
		expect(summary).toEqual({ shown: ['prompt', 'Checkpoint', 'steps'], more: 0, all: ['prompt', 'Checkpoint', 'steps'] });
	});

	it('falls back to the humanized key when the form has no label', () => {
		expect(humanizeKey('diffusion_model')).toBe('diffusion model');
		expect(versionChanges(version(2, 0, { changed_fields: ['cfg-scale'] }))?.all).toEqual(['cfg scale']);
	});

	it('shows at most three items and counts the rest', () => {
		const summary = versionChanges(
			version(2, 0, { changes: ['prompt', 'negative prompt'], changed_fields: ['a', 'b', 'c'] })
		)!;
		expect(summary.shown).toEqual(['prompt', 'negative prompt', 'a']);
		expect(summary.more).toBe(2);
		expect(summary.all).toEqual(['prompt', 'negative prompt', 'a', 'b', 'c']);
	});

	it('reports none when absent or empty', () => {
		expect(versionChanges(version(2, 0, { changes: [], changed_fields: [] }))).toBeNull();
		expect(versionChanges(version(2, 0))).toBeNull();
	});

	it('words the discard consequence per action', () => {
		expect(discardConsequence({ kind: 'load' })).toBe('Loading another session replaces them.');
		expect(discardConsequence({ kind: 'restore', versionNumber: 7 })).toBe('Restoring version v7 replaces them.');
		expect(discardConsequence({ kind: 'new' })).toBe('Starting a new session clears them.');
	});
});

describe('pinned sessions', () => {
	const pinned = (id: string, name: string, daysAgo: number): Session => ({
		...session(id, name, daysAgo),
		pinned: true
	});

	it('splits pinned from the rest and keeps their order', () => {
		const list = [pinned('a', 'A', 3), session('b', 'B', 0), pinned('c', 'C', 1)];
		const { pinned: top, rest } = splitPinned(list);
		expect(top.map((s) => s.id)).toEqual(['a', 'c']);
		expect(rest.map((s) => s.id)).toEqual(['b']);
	});

	it('search narrows pinned sessions too', () => {
		const list = [pinned('a', 'Neon alley', 3), pinned('c', 'Forest', 1), session('b', 'Neon market', 0)];
		const { pinned: top } = splitPinned(filterSessions(list, 'neon'));
		expect(top.map((s) => s.id)).toEqual(['a']);
	});

	it('pins to the front of the pinned block and unpins back into updated order', () => {
		const list = [pinned('a', 'A', 3), session('b', 'B', 0), session('c', 'C', 5)];
		const pinnedC = applyPin(list, 'c', true);
		expect(pinnedC.map((s) => s.id)).toEqual(['c', 'a', 'b']);
		const unpinnedA = applyPin(pinnedC, 'a', false);
		expect(unpinnedA.map((s) => [s.id, !!s.pinned])).toEqual([
			['c', true],
			['b', false],
			['a', false]
		]);
	});

	it('leaves the list alone for an unknown id', () => {
		const list = [session('a', 'A', 0)];
		expect(applyPin(list, 'zzz', true)).toBe(list);
	});
});
