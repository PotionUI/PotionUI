// Persists whether the unified chat's history rail is collapsed. Read once on
// mount and written on every toggle (see UnifiedAIChat.svelte); defaults to
// collapsed so a first-time user sees the same layout the panel had before
// the rail existed.
import { storage } from './storage';

export const HISTORY_RAIL_COLLAPSED_KEY = 'unified-ai-chat-history-rail-collapsed';

export function loadHistoryRailCollapsed(): boolean {
	const raw = storage.get(HISTORY_RAIL_COLLAPSED_KEY);
	if (raw === null) return true;
	return raw !== 'false';
}

export function saveHistoryRailCollapsed(collapsed: boolean): void {
	storage.set(HISTORY_RAIL_COLLAPSED_KEY, collapsed ? 'true' : 'false');
}
