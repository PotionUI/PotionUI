import { describe, it, expect } from 'vitest';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { isDifferentIdentity, keysToPurge } from './identityScopedStorage';

describe('isDifferentIdentity', () => {
	it('is false when no prior identity is recorded (first login)', () => {
		expect(isDifferentIdentity(null, 'user-a')).toBe(false);
	});

	it('is false when the same user logs back in', () => {
		expect(isDifferentIdentity('user-a', 'user-a')).toBe(false);
	});

	it('is true when a different user logs in', () => {
		expect(isDifferentIdentity('user-a', 'user-b')).toBe(true);
	});
});

describe('keysToPurge', () => {
	it('purges the tab state key', () => {
		expect(keysToPurge([TABS_STORAGE_KEY])).toEqual([TABS_STORAGE_KEY]);
	});

	it('purges every unified-ai-chat identity key', () => {
		const keys = [
			'unified-ai-chat-session-id',
			'unified-ai-chat-config-id',
			'unified-ai-chat-pinned-tab',
			'unified-ai-chat-attach-image',
			'unified-ai-chat-enable-tools',
			'phrasebook-generation-config'
		];
		expect(keysToPurge(keys)).toEqual(keys);
	});

	it('purges per-mode disabled-tools keys by prefix', () => {
		expect(keysToPurge(['unified-ai-chat-disabled-tools:chat', 'unified-ai-chat-disabled-tools:agent'])).toEqual([
			'unified-ai-chat-disabled-tools:chat',
			'unified-ai-chat-disabled-tools:agent'
		]);
	});

	it('leaves device/UI preference keys untouched', () => {
		const uiKeys = [
			'auth_token',
			'remember_me',
			'theme',
			'unified-ai-chat-history-rail-collapsed',
			'generation-panel-active-drawer',
			'potionui-form-audience',
			'audio_player_volume',
			'autoSaveEnabled',
			'autoSaveInterval'
		];
		expect(keysToPurge(uiKeys)).toEqual([]);
	});

	it('purges only the matching subset out of a mixed key set', () => {
		const mixed = [TABS_STORAGE_KEY, 'theme', 'unified-ai-chat-config-id', 'audio_player_volume'];
		expect(keysToPurge(mixed)).toEqual([TABS_STORAGE_KEY, 'unified-ai-chat-config-id']);
	});
});
