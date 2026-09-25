import type { ModeBasedSessionData, SessionData } from '$lib/types/api';
import type { ModeState, Tab } from '$lib/types/tabs';
import { captureModeState } from '$lib/utils/modeState';

interface ModeContentSnapshot {
	prompt: string;
	negativePrompt: string;
	promptSegments: unknown;
	negativePromptSegments: unknown;
	promptTabs: unknown;
	activePromptTab: unknown;
	formData: unknown;
	videoDirector: unknown;
	musicDirector: unknown;
}

function normalizeModeContent(data: Partial<SessionData & ModeState> | undefined): ModeContentSnapshot {
	const d = data ?? {};
	return {
		prompt: d.prompt ?? '',
		negativePrompt: d.negativePrompt ?? '',
		promptSegments: d.segments ?? d.promptSegments ?? [],
		negativePromptSegments: d.negativeSegments ?? d.negativePromptSegments ?? [],
		promptTabs: d.promptTabs ?? null,
		activePromptTab: d.activePromptTab ?? null,
		formData: d.formData ?? {},
		videoDirector: d.videoDirector ?? null,
		musicDirector: d.musicDirector ?? null
	};
}

function modeContentDiffers(saved: SessionData | undefined, current: ModeState): boolean {
	return JSON.stringify(normalizeModeContent(saved)) !== JSON.stringify(normalizeModeContent(current));
}

export function tabHasUnsavedSessionChanges(tab: Tab): boolean {
	if (!tab.selectedSessionId) return false;
	if (tab.savedSessionSignature === undefined) return false;
	if (tab.savedSessionSignature === null) return true;

	let saved: ModeBasedSessionData;
	try {
		saved = JSON.parse(tab.savedSessionSignature) as ModeBasedSessionData;
	} catch {
		return false;
	}

	const activeMode = tab.selectedMode;
	if (activeMode && modeContentDiffers(saved[activeMode], captureModeState(tab))) {
		return true;
	}

	for (const [mode, state] of Object.entries(tab.modeStateByMode ?? {})) {
		if (mode === activeMode) continue;
		if (modeContentDiffers(saved[mode], state)) return true;
	}

	return false;
}

export function anyTabHasUnsavedSessionChanges(tabs: Tab[]): boolean {
	return tabs.some(tabHasUnsavedSessionChanges);
}

export interface BeforeUnloadLike {
	preventDefault(): void;
	returnValue: string;
}

export function handleBeforeUnload(event: BeforeUnloadLike, tabs: Tab[]): void {
	if (!anyTabHasUnsavedSessionChanges(tabs)) return;
	event.preventDefault();
	event.returnValue = '';
}
