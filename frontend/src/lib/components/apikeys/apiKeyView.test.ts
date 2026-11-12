import { describe, it, expect } from 'vitest';
import {
	isMigratedKey,
	keyBadge,
	keyFingerprint,
	keyLine,
	mcpKeysOf,
	pageOf,
	scopeLine,
	splitKeys,
	usedPhrase,
	visibleKeys
} from './apiKeyView';
import { makeKey, NOW } from './fixtures';

describe('usedPhrase', () => {
	it('says never used without a timestamp', () => {
		expect(usedPhrase(null, NOW)).toBe('never used');
	});

	it.each([
		['2026-11-11T08:00:00Z', 'used today'],
		['2026-11-10T08:00:00Z', 'used yesterday'],
		['2026-11-08T08:00:00Z', 'used 3 days ago'],
		['2026-11-04T08:00:00Z', 'used 1 week ago'],
		['2026-10-21T08:00:00Z', 'used 3 weeks ago'],
		['2026-10-29T08:00:00Z', 'used 1 week ago'],
		['2026-09-29T08:00:00Z', 'used Sep 29'],
		['2025-12-30T08:00:00Z', 'used Dec 30, 2025']
	])('formats %s as %s', (value, expected) => {
		expect(usedPhrase(value, NOW)).toBe(expected);
	});
});

describe('keyLine', () => {
	it('puts scopes, the MCP tool count and use on one line', () => {
		expect(keyLine(makeKey(), { now: NOW })).toBe('Read · MCP, 18 tools · used today · never expires');
	});

	it('says MCP only for a key with no HTTP scope', () => {
		const key = makeKey({ scopes: ['mcp'], mcp_tools: { exposed: 35, total: 44 }, last_used_at: '2026-11-10T08:00:00Z' });
		expect(keyLine(key, { now: NOW })).toBe('MCP only, 35 tools · used yesterday · never expires');
	});

	it('lists several HTTP scopes in a fixed order', () => {
		const key = makeKey({ scopes: ['mcp', 'content', 'read', 'generate'], mcp_tools: { exposed: 33, total: 44 }, last_used_at: null });
		expect(keyLine(key, { now: NOW })).toBe('Read, Generate, Change content · MCP, 33 tools · never used · never expires');
	});

	it('says paused for MCP while the administrator has it off', () => {
		expect(keyLine(makeKey(), { now: NOW, mcpPaused: true })).toBe('Read · MCP paused · used today · never expires');
		expect(keyLine(makeKey({ scopes: ['mcp'] }), { now: NOW, mcpPaused: true })).toBe('MCP only, paused · used today · never expires');
	});

	it('shows a plain read key without any MCP text', () => {
		expect(keyLine(makeKey({ scopes: ['read'] }), { now: NOW })).toBe('Read · used today · never expires');
	});

	it('collapses to full administration when admin is present', () => {
		expect(scopeLine(['read', 'generate', 'content', 'admin'])).toBe('Full administration');
	});

	it('shows when a key expires, with the year only when it is not this one', () => {
		expect(keyLine(makeKey({ expires_at: '2026-12-12T09:00:00Z' }), { now: NOW })).toBe('Read · MCP, 18 tools · used today · expires Dec 12');
		expect(keyLine(makeKey({ expires_at: '2027-02-09T09:00:00Z' }), { now: NOW })).toContain('expires Feb 9 2027');
	});

	it('leaves expiry off a revoked or expired key', () => {
		expect(keyLine(makeKey({ status: 'revoked', expires_at: '2026-12-12T09:00:00Z' }), { now: NOW })).not.toContain('expire');
		expect(keyLine(makeKey({ status: 'expired', expires_at: '2026-11-01T09:00:00Z' }), { now: NOW })).not.toContain('expire');
	});

	it('uses the singular for one tool', () => {
		const key = makeKey({ mcp_tools: { exposed: 1, total: 44 } });
		expect(keyLine(key, { now: NOW })).toContain('MCP, 1 tool ·');
	});
});

describe('keyBadge', () => {
	it('shows nothing for a normal key', () => {
		expect(keyBadge(makeKey(), NOW)).toBeNull();
		expect(keyBadge(makeKey({ expires_at: '2027-02-09T09:00:00Z' }), NOW)).toBeNull();
	});

	it('warns when the key expires within a week', () => {
		expect(keyBadge(makeKey({ expires_at: '2026-11-17T12:00:00Z' }), NOW)).toEqual({ variant: 'warning', label: 'Expires in 6 days' });
		expect(keyBadge(makeKey({ expires_at: '2026-11-11T20:00:00Z' }), NOW)).toEqual({ variant: 'warning', label: 'Expires today' });
	});

	it('labels expired and revoked keys neutrally', () => {
		expect(keyBadge(makeKey({ status: 'expired' }), NOW)).toEqual({ variant: 'neutral', label: 'Expired' });
		expect(keyBadge(makeKey({ status: 'revoked' }), NOW)).toEqual({ variant: 'neutral', label: 'Revoked' });
	});
});

describe('splitKeys, visibleKeys and pageOf', () => {
	const keys = [
		makeKey({ id: 'a' }),
		makeKey({ id: 'b', status: 'revoked' }),
		makeKey({ id: 'c' }),
		makeKey({ id: 'd', status: 'expired' })
	];

	it('keeps active keys first and inactive behind the toggle', () => {
		const rows = splitKeys(keys);
		expect(rows.active.map((k) => k.id)).toEqual(['a', 'c']);
		expect(visibleKeys(rows, false).map((k) => k.id)).toEqual(['a', 'c']);
		expect(visibleKeys(rows, true).map((k) => k.id)).toEqual(['a', 'c', 'b', 'd']);
	});

	it('pages in tens and clamps the requested page', () => {
		const many = Array.from({ length: 12 }, (_, i) => makeKey({ id: `k${i}` }));
		expect(pageOf(many, 1)).toMatchObject({ pages: 2, page: 1 });
		expect(pageOf(many, 1).rows).toHaveLength(10);
		expect(pageOf(many, 2).rows.map((k) => k.id)).toEqual(['k10', 'k11']);
		expect(pageOf(many, 9).page).toBe(2);
		expect(pageOf([], 1)).toEqual({ rows: [], page: 1, pages: 1 });
	});
});

describe('key identity helpers', () => {
	it('joins prefix and last four', () => {
		expect(keyFingerprint(makeKey())).toBe('pui_7Hq2xK9a…Qz0P');
	});

	it('recognises a migrated MCP token by its prefix and missing last four', () => {
		expect(isMigratedKey(makeKey({ display_prefix: 'pui_mcp_a81f', last_four: null }))).toBe(true);
		expect(isMigratedKey(makeKey())).toBe(false);
	});

	it('offers only active keys with MCP to the tools picker', () => {
		const keys = [makeKey({ id: 'a' }), makeKey({ id: 'b', scopes: ['read'] }), makeKey({ id: 'c', status: 'revoked' })];
		expect(mcpKeysOf(keys).map((k) => k.id)).toEqual(['a']);
	});
});
