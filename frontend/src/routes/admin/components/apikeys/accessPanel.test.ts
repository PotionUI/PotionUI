import { describe, it, expect } from 'vitest';
import { makeListing, makeTool } from '$lib/components/apikeys/fixtures';
import {
	ceilingLowered,
	ceilingPatchOf,
	keysEnabledOf,
	keysSwitchNote,
	lifetimeNote,
	lifetimeOf,
	lifetimeOptions,
	mcpSwitchNote,
	mergeChange,
	netChange,
	pendingCount,
	scopeLoweredNote,
	scopesAllowedOf,
	withScope
} from './accessPanel';

describe('instance switches', () => {
	it('reads the API keys switch, on by default', () => {
		expect(keysEnabledOf({})).toBe(true);
		expect(keysEnabledOf({ api_keys_enabled: false })).toBe(false);
		expect(keysEnabledOf({ api_keys_enabled: 'false' })).toBe(false);
		expect(keysEnabledOf({ api_keys_enabled: 'true' })).toBe(true);
	});
});

describe('scope ceiling', () => {
	it('defaults to everything except admin and always includes read', () => {
		expect(scopesAllowedOf({})).toEqual(['read', 'generate', 'content', 'mcp']);
		expect(scopesAllowedOf({ api_key_scopes_allowed: ['generate'] })).toEqual(['read', 'generate']);
	});

	it('accepts a JSON string and falls back to the defaults on garbage', () => {
		expect(scopesAllowedOf({ api_key_scopes_allowed: '["read","admin"]' })).toEqual(['read', 'admin']);
		expect(scopesAllowedOf({ api_key_scopes_allowed: 'not json' })).toEqual(['read', 'generate', 'content', 'mcp']);
	});

	it('adds and removes one scope without touching the others, mcp included', () => {
		const settings = { api_key_scopes_allowed: ['read', 'generate', 'mcp'] };
		expect(withScope(settings, 'admin', true)).toEqual(['read', 'generate', 'admin', 'mcp']);
		expect(withScope(settings, 'generate', false)).toEqual(['read', 'mcp']);
	});

	it('gives the same list when a switch is flipped and flipped back', () => {
		const settings = { api_key_scopes_allowed: ['read', 'generate', 'content', 'mcp'] };
		const off = withScope(settings, 'content', false);
		expect(withScope({ api_key_scopes_allowed: off }, 'content', true)).toEqual(settings.api_key_scopes_allowed);
	});

	it('keeps a scope name this build does not know', () => {
		expect(withScope({ api_key_scopes_allowed: ['read', 'future'] }, 'generate', true)).toEqual(['read', 'generate', 'future']);
	});
});

describe('longest key lifetime', () => {
	it('reads days, with 0 meaning no limit', () => {
		expect(lifetimeOf({})).toBe(0);
		expect(lifetimeOf({ api_key_max_lifetime_days: 90 })).toBe(90);
		expect(lifetimeOf({ api_key_max_lifetime_days: '30' })).toBe(30);
		expect(lifetimeOf({ api_key_max_lifetime_days: -5 })).toBe(0);
	});

	it('offers the four choices and adds the stored value when it is none of them', () => {
		expect(lifetimeOptions(90).map((o) => o.label)).toEqual(['No limit', '1 year', '90 days', '30 days']);
		expect(lifetimeOptions(45).map((o) => o.label)).toEqual(['No limit', '1 year', '90 days', '45 days', '30 days']);
	});

	it('says new keys only and the admin cap', () => {
		expect(lifetimeNote(0)).toContain('90 days');
		expect(lifetimeNote(30)).toContain('Existing keys keep their expiry');
		expect(lifetimeNote(30)).toContain('30 days at most');
		expect(lifetimeNote(365)).toContain('90 days at most');
	});
});

describe('what happens to existing keys', () => {
	it('warns on save when a switch is being turned off and says nothing when on', () => {
		expect(keysSwitchNote(true, false)?.tone).toBe('warning');
		expect(keysSwitchNote(true, false)?.text).toContain('within seconds');
		expect(keysSwitchNote(true, false)?.text).toContain('not deleted');
		expect(keysSwitchNote(false, false)?.tone).toBe('info');
		expect(keysSwitchNote(true, true)).toBeNull();
		expect(mcpSwitchNote(true, false)?.text).toContain('open connections');
		expect(mcpSwitchNote(false, true)).toBeNull();
	});

	it('warns only when a scope that was allowed is being removed', () => {
		expect(scopeLoweredNote(true, false)?.tone).toBe('warning');
		expect(scopeLoweredNote(false, false)).toBeNull();
		expect(scopeLoweredNote(true, true)).toBeNull();
	});
});

describe('ceiling draft', () => {
	const base = makeListing(
		[
			makeTool({ name: 'a', writes: true }),
			makeTool({ name: 'b' }),
			makeTool({ name: 'c', state: 'off' }),
			makeTool({ name: 'n', reviewed: false })
		],
		{ level: 'ceiling', policy: 'all_on' }
	);

	it('merges successive changes, the later value winning', () => {
		const merged = mergeChange(mergeChange({}, { overrides: { a: false } }), { policy: 'read_only_on', overrides: { a: true, b: false } });
		expect(merged).toEqual({ policy: 'read_only_on', overrides: { a: true, b: false } });
	});

	it('drops an override that puts a reviewed tool back where it was', () => {
		expect(netChange(base, { overrides: { a: true, b: false } })).toEqual({ overrides: { b: false } });
		expect(netChange(base, { policy: 'all_on' })).toEqual({});
	});

	it('keeps an override on an unreviewed tool, because it records a decision', () => {
		expect(netChange(base, { overrides: { n: true } })).toEqual({ overrides: { n: true } });
	});

	it('counts what the Save bar will send', () => {
		expect(pendingCount(null, { overrides: { a: false } })).toBe(0);
		expect(pendingCount(base, {})).toBe(0);
		expect(pendingCount(base, { policy: 'read_only_on', overrides: { a: false, b: false } })).toBe(3);
	});

	it('has no patch for no net change', () => {
		expect(ceilingPatchOf(base, { overrides: { a: true } })).toBeNull();
		expect(ceilingPatchOf(base, { overrides: { b: false } })).toEqual({ overrides: { b: false } });
	});

	it('knows when the change lowers the ceiling', () => {
		expect(ceilingLowered(base, { overrides: { b: false } })).toBe(true);
		expect(ceilingLowered(base, { overrides: { c: true } })).toBe(false);
		expect(ceilingLowered(base, { policy: 'read_only_on' })).toBe(true);
		expect(ceilingLowered(base, {})).toBe(false);
		expect(ceilingLowered(null, { overrides: { b: false } })).toBe(false);
	});
});
