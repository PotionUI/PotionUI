import type { ChatToolInfo } from '$lib/types/chat';
import type { UserToolPreference } from '$lib/types/llm';

/**
 * The mode's tool list filtered down to tools this user may see at all
 * (admin-`enabled`). `preferences === null` means "not loaded yet" - shown as
 * a passthrough so the Tools popover behaves exactly as it did before this
 * feature existed while the preferences fetch is in flight. Admin-disabled
 * tools are omitted entirely, not shown as unavailable - see
 * `src.features.llm.tools.governance.build_user_toolset_listing`, which the
 * `/api/llm/toolset/preferences` response already applies server-side; this
 * mirrors that same omission for the mode's already-fetched tool catalog.
 */
export function filterVisibleToolsByPreferences(
	tools: ChatToolInfo[],
	preferences: UserToolPreference[] | null
): ChatToolInfo[] {
	if (preferences === null) return tools;
	const adminEnabledNames = new Set(preferences.map((p) => p.name));
	return tools.filter((t) => adminEnabledNames.has(t.name));
}
