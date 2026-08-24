import { TABS_STORAGE_KEY } from '$lib/types/tabs';

// localStorage keys that hold content tied to a specific signed-in identity
// (open tabs with their prompts/presets, the active chat session, the
// selected LLM config, the chat behavior toggles, the phrasebook preview
// config). Logging in as a DIFFERENT user must not surface these — see
// keysToPurge()/LAST_USER_ID_KEY below. UI-only prefs (theme, panel widths,
// sound toggles, item-per-page counts, ...) are device state rather than
// identity state and are deliberately left out of this list.
export const IDENTITY_SCOPED_STORAGE_KEYS: readonly string[] = [
	TABS_STORAGE_KEY,
	'unified-ai-chat-session-id',
	'unified-ai-chat-config-id',
	'unified-ai-chat-pinned-tab',
	'unified-ai-chat-attach-image',
	'unified-ai-chat-enable-tools',
	'phrasebook-generation-config'
];

// `unified-ai-chat-disabled-tools:<mode>` is keyed per chat mode, so it's
// matched by prefix rather than enumerated. The legacy per-mode session-id
// keys (`unified-ai-chat-session-id:<mode>`, migrated by chatConfig.ts) are
// identity state too and must not survive a user switch to be migrated into
// the new user's session pointer.
export const IDENTITY_SCOPED_STORAGE_PREFIXES: readonly string[] = [
	'unified-ai-chat-disabled-tools:',
	'unified-ai-chat-session-id:'
];

// Records which user id last populated the identity-scoped keys above, so a
// same-user relogin (e.g. after a session expiry) is told apart from an
// actual switch to a different account.
export const LAST_USER_ID_KEY = 'auth_last_user_id';

export function isDifferentIdentity(lastUserId: string | null, currentUserId: string): boolean {
	return lastUserId !== null && lastUserId !== currentUserId;
}

/** Pure filter: given every key currently in storage, which ones to drop. */
export function keysToPurge(allKeys: readonly string[]): string[] {
	return allKeys.filter(
		(key) =>
			IDENTITY_SCOPED_STORAGE_KEYS.includes(key) ||
			IDENTITY_SCOPED_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix))
	);
}
