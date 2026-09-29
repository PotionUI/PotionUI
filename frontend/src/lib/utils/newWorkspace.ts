import type { Tab } from '$lib/types/tabs';
import { tabHasUnsavedSessionChanges } from '$lib/utils/unsavedChangesGuard';

function formDiffersFromBaseline(tab: Tab): boolean {
	if (!tab.formPublished || tab.formBaselineSignature === undefined) return false;
	return JSON.stringify(tab.formData ?? {}) !== tab.formBaselineSignature;
}

export function tabHasUnsavedWork(tab: Tab): boolean {
	if (tab.selectedSessionId) return tabHasUnsavedSessionChanges(tab);
	return !!(
		tab.prompt?.trim() ||
		tab.negativePrompt?.trim() ||
		(tab.promptSegments && tab.promptSegments.length > 0) ||
		(tab.negativePromptSegments && tab.negativePromptSegments.length > 0) ||
		(tab.variables && Object.keys(tab.variables).length > 0) ||
		formDiffersFromBaseline(tab)
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
