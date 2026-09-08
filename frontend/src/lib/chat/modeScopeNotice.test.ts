import { describe, expect, it } from 'vitest';
import { deriveModeScopeMismatch } from './modeScopeNotice';
import type { ChatMode } from '$lib/types/chat';

function mode(id: string, name: string): ChatMode {
	return {
		id,
		name,
		description: '',
		default_route_prefixes: [],
		tools: [],
		source: 'core'
	};
}

const modes: ChatMode[] = [mode('history', 'History'), mode('models', 'Models')];

describe('deriveModeScopeMismatch', () => {
	it('reports both scope names when locked and the page has moved on', () => {
		const mismatch = deriveModeScopeMismatch('history', 'models', modes, true);
		expect(mismatch).toEqual({ conversationModeName: 'History', pageModeName: 'Models' });
	});

	it('is null while the conversation is unlocked (chip is still editable)', () => {
		expect(deriveModeScopeMismatch('history', 'models', modes, false)).toBeNull();
	});

	it('is null when the conversation and page scopes agree', () => {
		expect(deriveModeScopeMismatch('history', 'history', modes, true)).toBeNull();
	});

	it('is null when the page scope has not resolved yet', () => {
		expect(deriveModeScopeMismatch('history', null, modes, true)).toBeNull();
	});

	it('falls back to the raw id when a mode is unknown', () => {
		const mismatch = deriveModeScopeMismatch('history', 'unknown-scope', modes, true);
		expect(mismatch).toEqual({ conversationModeName: 'History', pageModeName: 'unknown-scope' });
	});
});
