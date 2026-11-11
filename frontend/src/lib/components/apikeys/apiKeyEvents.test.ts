import { describe, it, expect } from 'vitest';
import type { ApiKeyEvent } from '$lib/services/api/apiKeys';
import { describeEvent, describeEvents } from './apiKeyEvents';
import { NOW } from './fixtures';

const ev = (over: Partial<ApiKeyEvent>): ApiKeyEvent => ({ action: 'created', actor: null, detail: {}, changes: null, created_at: '2026-10-22T09:00:00Z', ...over });

describe('describeEvent', () => {
	it('words each action in plain words with a short date', () => {
		expect(describeEvent(ev({}), NOW)).toEqual({ when: 'Oct 22', what: 'Created', detail: '' });
		expect(describeEvent(ev({ action: 'renamed', changes: { name: { before: 'Claude', after: 'Claude Desktop' } } }), NOW).detail).toBe('From “Claude” to “Claude Desktop”');
		expect(describeEvent(ev({ action: 'expired' }), NOW).what).toBe('Expired');
	});

	it('lists scopes added and removed by name', () => {
		const line = describeEvent(ev({ action: 'scopes_changed', changes: { scopes: { added: ['generate'], removed: ['mcp'] } } }), NOW);
		expect(line).toMatchObject({ what: 'Access changed', detail: 'Can generate added, Use with MCP clients removed' });
	});

	it('says who revoked', () => {
		expect(describeEvent(ev({ action: 'revoked', detail: { by_owner: true } }), NOW).detail).toBe('By you');
		expect(describeEvent(ev({ action: 'revoked', detail: { by_owner: false } }), NOW).detail).toBe('By an administrator');
	});

	it('survives an unknown action and bounds the list', () => {
		expect(describeEvent(ev({ action: 'mcp_tools_changed' }), NOW).what).toBe('Mcp tools changed');
		expect(describeEvents(Array.from({ length: 9 }, () => ev({})), NOW)).toHaveLength(4);
	});
});
