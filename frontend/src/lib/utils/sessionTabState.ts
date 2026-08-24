import type { ModeBasedSessionData } from '$lib/services/api';
import type { Tab } from '$lib/types/tabs';
import { DEFAULT_PROMPT_PANEL_WIDTH } from '$lib/stores/generationLayout';
import { mergeCachedModesIntoSessionData } from '$lib/utils/modeState';

/**
 * Build the exact multi-mode payload a generation tab would save. Keeping this
 * outside SessionPill lets page-level session hydration record the same
 * saved baseline that the pill compares against after it is remounted.
 */
export function collectTabSessionData(
	tab: Tab | undefined,
	currentMode: string | null | undefined,
	savedSessionData: ModeBasedSessionData = {},
	presetVersion?: string
): ModeBasedSessionData {
	if (!tab || !currentMode) return {};

	return {
		...mergeCachedModesIntoSessionData(savedSessionData, tab.modeStateByMode),
		[currentMode]: {
			selectedPreset: tab.selectedPreset || undefined,
			selectedMode: currentMode,
			selectedVariant: tab.selectedVariant || undefined,
			presetVersion,
			prompt: tab.prompt,
			negativePrompt: tab.negativePrompt,
			promptSegments: tab.promptSegments || [],
			negativePromptSegments: tab.negativePromptSegments || [],
			promptTabs: tab.promptTabs,
			activePromptTab: tab.activePromptTab,
			promptRelay: tab.promptRelay,
			videoDirector: tab.videoDirector,
			musicDirector: tab.musicDirector,
			formData: tab.formData,
			...(tab.variables && Object.keys(tab.variables).length > 0
				? { variables: tab.variables }
				: {}),
			seed: tab.seed,
			selectedBackendId: tab.selectedBackendId || undefined,
			workbenchMaxHeight: tab.workbenchMaxHeight || '600',
			leftPanelWidth: tab.leftPanelWidth || 380,
			...(typeof tab.leftPanelCollapsed === 'boolean'
				? { leftPanelCollapsed: tab.leftPanelCollapsed }
				: {}),
			layoutMode: tab.layoutMode,
			promptPanelWidth: tab.promptPanelWidth || DEFAULT_PROMPT_PANEL_WIDTH,
			positiveSegmentsCollapsed: tab.positiveSegmentsCollapsed,
			negativeSegmentsCollapsed: tab.negativeSegmentsCollapsed,
			...(tab.sectionCollapsed && Object.keys(tab.sectionCollapsed).length > 0
				? { sectionCollapsed: tab.sectionCollapsed }
				: {})
		}
	};
}

/** A remount has an existing id but is not a request to reload its server data. */
export function shouldHydrateSessionSelection(
	hasMounted: boolean,
	previousSessionId: string,
	nextSessionId: string | null | undefined
): boolean {
	return hasMounted && !!nextSessionId && nextSessionId !== previousSessionId;
}

/** Null is an intentional "historical restore is dirty" baseline. */
export function sessionIsDirty(
	hasSession: boolean,
	savedSignature: string | null,
	currentSignature: string | null
): boolean {
	if (!hasSession) return false;
	if (savedSignature === null) return true;
	return currentSignature !== null && currentSignature !== savedSignature;
}

/**
 * Sessions can predate newly-added schema defaults. DynamicForm merges those
 * defaults before its first publication, so update just the active mode's
 * saved form baseline once instead of marking an untouched session dirty.
 */
export function normalizeSessionBaselineFormData(
	savedSignature: string | null | undefined,
	currentMode: string | null | undefined,
	formData: Record<string, unknown>
): string | null {
	if (!savedSignature || !currentMode) return savedSignature ?? null;

	try {
		const savedData = JSON.parse(savedSignature) as ModeBasedSessionData;
		const modeData = savedData[currentMode];
		if (!modeData) return savedSignature;

		return JSON.stringify({
			...savedData,
			[currentMode]: { ...modeData, formData }
		});
	} catch {
		return savedSignature;
	}
}
