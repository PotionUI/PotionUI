import { describe, it, expect } from 'vitest';
import {
	applyChange,
	bulkAction,
	buildGroups,
	changeForPolicy,
	changeForReset,
	changeForTools,
	changeForWritesOff,
	countTools,
	isEmptyChange,
	isGroupOpen,
	keyPatchOf,
	policyOptions,
	showsNewChip,
	summaryText,
	toolKind,
	toolMatches
} from './mcpToolsView';
import type { McpTool } from '$lib/services/api/mcpTools';
import { makeListing, makeTool, SAMPLE_TOOLS } from './fixtures';

const byName = (tools: McpTool[], name: string) => tools.find((t) => t.name === name)!;

describe('counts and labels', () => {
	it('counts usable tools without unavailable and locked ones', () => {
		expect(countTools(SAMPLE_TOOLS)).toEqual({ on: 3, usable: 6, unavailable: 1, locked: 1, offDefault: 1 });
	});

	it('words the summary per level', () => {
		const counts = countTools(SAMPLE_TOOLS);
		expect(summaryText('default', counts)).toBe('3 of 6 on');
		expect(summaryText('ceiling', counts)).toBe('3 of 6 allowed');
	});

	it('offers one secondary action per level', () => {
		expect(bulkAction('default')).toEqual({ id: 'writes-off', label: 'Turn off writes' });
		expect(bulkAction('key')).toEqual({ id: 'match', label: 'Match default' });
	});

	it('words the policy options per level', () => {
		expect(policyOptions('default').map((o) => o.label)).toEqual(['All on', 'Read-only on']);
		expect(policyOptions('ceiling').map((o) => o.label)).toEqual(['All tools', 'Read-only tools only']);
	});

	it('maps the five states onto four kinds', () => {
		expect(SAMPLE_TOOLS.map(toolKind)).toEqual(['tool', 'unavailable', 'tool', 'admin', 'tool', 'tool', 'tool', 'offDefault']);
	});

	it('shows the new chip only for a reviewable tool that was not reviewed', () => {
		expect(showsNewChip(makeTool({ reviewed: false }))).toBe(true);
		expect(showsNewChip(makeTool({ reviewed: false, state: 'off_in_default' }))).toBe(true);
		expect(showsNewChip(makeTool({ reviewed: false, state: 'locked_by_admin' }))).toBe(false);
		expect(showsNewChip(makeTool({ reviewed: true }))).toBe(false);
	});
});

describe('filters and search', () => {
	it('filters on, off and can’t run', () => {
		const names = (filter: 'on' | 'off' | 'unavailable') => SAMPLE_TOOLS.filter((t) => toolMatches(t, { query: '', filter })).map((t) => t.name);
		expect(names('on')).toEqual(['start_generation', 'search_gallery', 'list_presets']);
		expect(names('off')).toEqual(['organize_gallery', 'delete_prompt', 'list_prompts']);
		expect(names('unavailable')).toEqual(['run_generation']);
	});

	it('searches name, description and group case-insensitively', () => {
		const hit = (query: string) => SAMPLE_TOOLS.filter((t) => toolMatches(t, { query, filter: 'all' })).map((t) => t.name);
		expect(hit('GALLERY')).toEqual(['search_gallery', 'organize_gallery']);
		expect(hit('lists the prompts')).toEqual(['list_prompts']);
		expect(hit('models & pre')).toEqual(['list_presets']);
	});
});

describe('groups', () => {
	it('keeps catalogue order and counts on over usable', () => {
		const groups = buildGroups(SAMPLE_TOOLS, { query: '', filter: 'all' });
		expect(groups.map((g) => g.name)).toEqual(['Generation', 'Models & presets', 'Saved prompts']);
		expect(groups[0]).toMatchObject({ on: 2, denominator: 3, allOn: false, mixed: true });
		expect(groups[1]).toMatchObject({ on: 1, denominator: 1, allOn: true, mixed: false });
	});

	it('drops groups with nothing to show and keeps the denominator whole', () => {
		const groups = buildGroups(SAMPLE_TOOLS, { query: '', filter: 'unavailable' });
		expect(groups.map((g) => g.name)).toEqual(['Generation']);
		expect(groups[0].shown.map((t) => t.name)).toEqual(['run_generation']);
		expect(groups[0].denominator).toBe(3);
	});

	it('opens the default group, honours a choice, and opens everything while searching or filtering', () => {
		const all = { query: '', filter: 'all' as const };
		expect(isGroupOpen('A', all, {}, 'A')).toBe(true);
		expect(isGroupOpen('B', all, {}, 'A')).toBe(false);
		expect(isGroupOpen('A', all, { A: false }, 'A')).toBe(false);
		expect(isGroupOpen('B', all, { B: true }, 'A')).toBe(true);
		expect(isGroupOpen('B', { query: 'x', filter: 'all' }, { B: false }, 'A')).toBe(true);
		expect(isGroupOpen('B', { query: '', filter: 'on' }, {}, 'A')).toBe(true);
	});
});

describe('changes', () => {
	it('sends true and false at the default level and false or null at a key', () => {
		const tools = [makeTool({ name: 'a' }), makeTool({ name: 'b', state: 'locked_by_admin' }), makeTool({ name: 'c', state: 'unavailable' })];
		expect(changeForTools('default', tools, false)).toEqual({ overrides: { a: false } });
		expect(changeForTools('default', tools, true)).toEqual({ overrides: { a: true } });
		expect(changeForTools('key', tools, false)).toEqual({ overrides: { a: false } });
		expect(changeForTools('key', tools, true)).toEqual({ overrides: { a: null } });
	});

	it('turns off only the writing tools that are on', () => {
		expect(changeForWritesOff('default', SAMPLE_TOOLS)).toEqual({ overrides: { start_generation: false } });
	});

	it('shapes the key patch so a key can only narrow', () => {
		expect(keyPatchOf({ overrides: { a: true, b: false, c: null } })).toEqual({ overrides: { a: null, b: false, c: null } });
		expect(keyPatchOf(changeForReset())).toEqual({ reset: true });
	});

	it('knows an empty change', () => {
		expect(isEmptyChange({ overrides: {} })).toBe(true);
		expect(isEmptyChange(changeForPolicy('all_on'))).toBe(false);
		expect(isEmptyChange(changeForReset())).toBe(false);
	});
});

describe('applyChange (optimistic)', () => {
	it('flips one tool at the default level and marks it reviewed', () => {
		const listing = makeListing([makeTool({ name: 'a', reviewed: false, state: 'on' })]);
		const next = applyChange(listing, { overrides: { a: false } });
		expect(next.tools[0]).toMatchObject({ state: 'off', reviewed: true });
		expect(listing.tools[0].state).toBe('on');
	});

	it('clearing an override returns a tool to the policy and to unreviewed', () => {
		const listing = makeListing([makeTool({ name: 'w', writes: true, state: 'on', reviewed: true })], { policy: 'read_only_on' });
		const next = applyChange(listing, { overrides: { w: null } });
		expect(next.tools[0]).toMatchObject({ state: 'off', reviewed: false });
	});

	it('treats a tool that spends like a write under the read-only policy', () => {
		const listing = makeListing([makeTool({ name: 'enhance_prompt', spends: true, state: 'off', reviewed: false })], { policy: 'read_only_on' });
		expect(applyChange(listing, changeForPolicy('all_on')).tools[0].state).toBe('on');
		const on = makeListing([makeTool({ name: 'enhance_prompt', spends: true, state: 'on', reviewed: true })]);
		expect(applyChange(on, { overrides: { enhance_prompt: null } }).tools[0].state).toBe('off');
	});

	it('moves only unreviewed tools when the policy changes', () => {
		const listing = makeListing(
			[
				makeTool({ name: 'new_write', writes: true, state: 'off', reviewed: false }),
				makeTool({ name: 'old_write', writes: true, state: 'off', reviewed: true }),
				makeTool({ name: 'new_read', state: 'on', reviewed: false })
			],
			{ policy: 'read_only_on' }
		);
		const all = applyChange(listing, changeForPolicy('all_on'));
		expect(all.policy).toBe('all_on');
		expect(all.tools.map((t) => t.state)).toEqual(['on', 'off', 'on']);
		const back = applyChange(all, changeForPolicy('read_only_on'));
		expect(back.tools.map((t) => t.state)).toEqual(['off', 'off', 'on']);
	});

	it('never touches locked, unavailable or off-in-default tools', () => {
		const listing = makeListing(SAMPLE_TOOLS);
		const everything = Object.fromEntries(SAMPLE_TOOLS.map((t) => [t.name, true]));
		const next = applyChange(listing, { overrides: everything });
		expect(byName(next.tools, 'run_media_tool').state).toBe('locked_by_admin');
		expect(byName(next.tools, 'run_generation').state).toBe('unavailable');
		expect(byName(next.tools, 'list_prompts').state).toBe('off_in_default');
		expect(byName(next.tools, 'delete_prompt').state).toBe('on');
	});

	it('narrows at a key and clears the narrowing again', () => {
		const listing = makeListing([makeTool({ name: 'a', state: 'on' }), makeTool({ name: 'b', state: 'off' })], { level: 'key', key_id: 'k1', policy: undefined });
		expect(applyChange(listing, { overrides: { a: false } }).tools.map((t) => t.state)).toEqual(['off', 'off']);
		expect(applyChange(listing, { overrides: { b: null } }).tools.map((t) => t.state)).toEqual(['on', 'on']);
	});

	it('resets a key to its default', () => {
		const listing = makeListing([makeTool({ name: 'a', state: 'off' }), makeTool({ name: 'b', state: 'off_in_default' })], { level: 'key', key_id: 'k1' });
		expect(applyChange(listing, changeForReset()).tools.map((t) => t.state)).toEqual(['on', 'off_in_default']);
	});

	it('keeps the group switch state consistent after a group toggle', () => {
		const listing = makeListing(SAMPLE_TOOLS);
		const group = buildGroups(listing.tools, { query: '', filter: 'all' })[0];
		const next = applyChange(listing, changeForTools('default', group.toggleable, true));
		const after = buildGroups(next.tools, { query: '', filter: 'all' })[0];
		expect(after).toMatchObject({ on: 3, allOn: true, mixed: false });
	});
});
