import { describe, it, expect } from 'vitest';
import { describeOrigin, EDITOR_ORIGIN_LABEL, offersOpenInTab, ORIGIN_FILTER_OPTIONS, originFilterLabel } from './origin';
import { ORIGIN_LABEL } from '$lib/tool-tabs/editOrigin';

describe('describeOrigin', () => {
	it('names the key an MCP generation came from, preferring its current name', () => {
		expect(describeOrigin({ kind: 'mcp', key_id: 'k1', key_name: 'old name', key_current_name: 'claude desktop' })).toEqual({
			kind: 'mcp',
			label: 'Started over MCP · claude desktop',
			icon: 'plug'
		});
	});

	it('falls back to the stored key name when the key was deleted or has no current name', () => {
		expect(describeOrigin({ kind: 'mcp', key_id: 'k1', key_name: 'claude desktop', key_current_name: null })?.label).toBe(
			'Started over MCP · claude desktop'
		);
		expect(describeOrigin({ kind: 'mcp', key_id: 'k1', key_name: 'claude desktop' })?.label).toBe(
			'Started over MCP · claude desktop'
		);
	});

	it('drops the name part when no name is known at all', () => {
		expect(describeOrigin({ kind: 'mcp', key_id: 'k1', key_name: '  ', key_current_name: '' })?.label).toBe('Started over MCP');
	});

	it('labels editor and chat generations', () => {
		expect(describeOrigin({ kind: 'editor', edit_id: 'e1' })).toEqual({ kind: 'editor', label: 'Made in the editor', icon: 'sliders' });
		expect(describeOrigin({ kind: 'chat' })).toEqual({ kind: 'chat', label: 'Started from chat', icon: 'chat' });
	});

	it('has nothing to say about a tab-started generation or an unknown kind', () => {
		expect(describeOrigin(null)).toBeNull();
		expect(describeOrigin(undefined)).toBeNull();
		expect(describeOrigin({ kind: 'tab' })).toBeNull();
		expect(describeOrigin({ kind: 'something-new' })).toBeNull();
	});

	it('keeps the editor wording in step with the editor chip', () => {
		expect(EDITOR_ORIGIN_LABEL).toBe(ORIGIN_LABEL);
	});
});

describe('offersOpenInTab', () => {
	it('is for a generation started over MCP, at any stage', () => {
		for (const status of ['pending', 'running', 'completed', 'failed']) {
			expect(offersOpenInTab({ origin: { kind: 'mcp', key_id: 'k' }, status } as never)).toBe(true);
		}
	});

	it('is for nothing else: not the editor, not chat, not a tab, not a running one without a source', () => {
		expect(offersOpenInTab({ origin: { kind: 'editor', edit_id: 'e1' } })).toBe(false);
		expect(offersOpenInTab({ origin: { kind: 'chat' } })).toBe(false);
		expect(offersOpenInTab({ origin: { kind: 'tab' } })).toBe(false);
		expect(offersOpenInTab({ origin: null })).toBe(false);
		expect(offersOpenInTab({ status: 'running', origin: null } as never)).toBe(false);
	});
});

describe('the source filter choices', () => {
	it('offers the four sources the server filters on, in plain words', () => {
		expect(ORIGIN_FILTER_OPTIONS.map((option) => option.value)).toEqual(['mcp', 'editor', 'chat', 'tab']);
		expect(originFilterLabel('mcp')).toBe('Started over MCP');
		expect(originFilterLabel('tab')).toBe('Started in a tab');
		expect(originFilterLabel(undefined)).toBe('');
	});
});
