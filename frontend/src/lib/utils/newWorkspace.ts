import type { Tab } from '$lib/types/tabs';

/**
 * A tab carries unsaved workspace state if either:
 *  - it's bound to a session, and `savedSessionSignature === null` — the
 *    deliberate "historical restore is dirty" baseline from
 *    sessionTabState.ts's `sessionIsDirty` (same cheap cross-tab signal
 *    StudioPresetSessionSheet's `isTabDirty` already uses for its per-tab dot;
 *    a full diff needs the tab's live prompt/form state, which only exists
 *    for the ACTIVE tab's mounted SessionCluster — see
 *    `stores/workspaceDirtyQuery.ts` for how the active tab's real-time
 *    answer is layered on top of this at click time), or
 *  - it was never saved as a session at all, but has diverged from a
 *    pristine just-created tab (a preset picked, or any prompt/form content
 *    typed). An unsaved draft is still unsaved work — "dirty draft is
 *    authoritative", never silently discarded without asking.
 */
export function tabHasUnsavedWork(tab: Tab): boolean {
	if (tab.selectedSessionId) return tab.savedSessionSignature === null;
	return !!(
		tab.selectedPreset ||
		tab.prompt?.trim() ||
		tab.negativePrompt?.trim() ||
		(tab.promptSegments && tab.promptSegments.length > 0) ||
		(tab.negativePromptSegments && tab.negativePromptSegments.length > 0) ||
		(tab.formData && Object.keys(tab.formData).length > 0) ||
		(tab.variables && Object.keys(tab.variables).length > 0)
	);
}

export function workspaceHasUnsavedChanges(tabs: Tab[]): boolean {
	return tabs.some(tabHasUnsavedWork);
}

export type NewWorkspaceDecision = 'wipe' | 'confirm';

/** The click-time decision "New workspace" makes: wipe immediately when the
 *  workspace is clean, otherwise hand off to the 3-way confirm modal. */
export function decideNewWorkspaceAction(tabs: Tab[]): NewWorkspaceDecision {
	return workspaceHasUnsavedChanges(tabs) ? 'confirm' : 'wipe';
}

/** Whether any tab OTHER than the given one also carries unsaved work — used
 *  to warn that "Save & create new" only saves the active tab (the only one
 *  with a live session-save UI mounted; see `Tab.sessionDirty`'s doc
 *  comment), so a dirty background tab's edits are discarded regardless. */
export function hasUnsavedWorkOutsideTab(tabs: Tab[], tabId: string): boolean {
	return tabs.some((tab) => tab.id !== tabId && tabHasUnsavedWork(tab));
}
