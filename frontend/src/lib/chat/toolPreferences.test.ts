import { describe, expect, it } from 'vitest';
import { filterVisibleToolsByPreferences } from './toolPreferences';
import type { ChatToolInfo } from '$lib/types/chat';
import type { UserToolPreference } from '$lib/types/llm';

function tool(name: string): ChatToolInfo {
	return { name, description: '', hint: '' };
}

function preference(name: string, overrides: Partial<UserToolPreference> = {}): UserToolPreference {
	return {
		name,
		label: name,
		user_description: '',
		enabled_by_admin: true,
		locked: false,
		disabled_by_user: false,
		...overrides
	};
}

describe('filterVisibleToolsByPreferences', () => {
	it('passes every tool through while preferences have not loaded yet', () => {
		const tools = [tool('a'), tool('b')];
		expect(filterVisibleToolsByPreferences(tools, null)).toEqual(tools);
	});

	it('drops a tool with no matching preference (admin-disabled)', () => {
		const tools = [tool('a'), tool('b')];
		const prefs = [preference('a')];
		expect(filterVisibleToolsByPreferences(tools, prefs).map((t) => t.name)).toEqual(['a']);
	});

	it('keeps a tool that is locked - locked only affects the opt-out toggle, not visibility', () => {
		const tools = [tool('a')];
		const prefs = [preference('a', { locked: true })];
		expect(filterVisibleToolsByPreferences(tools, prefs).map((t) => t.name)).toEqual(['a']);
	});

	it('keeps a tool the user has personally disabled - that is a session/opt-out concern, not visibility', () => {
		const tools = [tool('a')];
		const prefs = [preference('a', { disabled_by_user: true })];
		expect(filterVisibleToolsByPreferences(tools, prefs).map((t) => t.name)).toEqual(['a']);
	});

	it('returns an empty list when preferences is an empty (loaded) array', () => {
		expect(filterVisibleToolsByPreferences([tool('a')], [])).toEqual([]);
	});

	it('preserves the original tool order', () => {
		const tools = [tool('b'), tool('a')];
		const prefs = [preference('a'), preference('b')];
		expect(filterVisibleToolsByPreferences(tools, prefs).map((t) => t.name)).toEqual(['b', 'a']);
	});
});
