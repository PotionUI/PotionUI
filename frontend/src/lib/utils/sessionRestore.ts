// The Tab fields a saved session's per-mode payload (`SessionData`) restores
// — shared by every place that applies one to a tab:
//   - lib/components/session/SessionPill.svelte (manual session picker)
//   - lib/stores/sessions.ts `loadSession` (programmatic load)
//   - routes/generate/+page.svelte `restoreTabSessions` (auto-restore on
//     mount, driven by `tab.selectedSessionId`)
//
// These three had drifted (duplicated field lists silently diverge — the
// auto-restore path was missing `variables`, `promptTabs`/`activePromptTab`,
// and `leftPanelCollapsed`). Pulling the common patch out into one pure
// function means a field added here can't go missing from one path again.
//
// Each call site keeps its own handling of fields that are genuinely
// call-site-specific: `selectedPreset`/`selectedSessionId`/`selectedMode`,
// and `selectedVariant` (SessionPill resolves it against live preset
// variants; +page.svelte's auto-restore deliberately does NOT — a downstream
// reactive block re-validates it once modes load, see its own comment).

import type { SessionData } from '$lib/types/api';
import type { Tab } from '$lib/types/tabs';

export interface SessionRestoreFallback {
	/** Current tab value to fall back to when the saved session doesn't specify one. */
	selectedBackendId?: string | null;
	promptPanelWidth?: number;
}

const DEFAULT_WORKBENCH_MAX_HEIGHT = '600';
const DEFAULT_LEFT_PANEL_WIDTH = 380;
const DEFAULT_PROMPT_PANEL_WIDTH = 420;

export function buildSessionRestoreTabPatch(
	modeData: SessionData,
	fallback: SessionRestoreFallback = {}
): Partial<Tab> {
	return {
		prompt: modeData.prompt || '',
		negativePrompt: modeData.negativePrompt || '',
		// `segments`/`negativeSegments` are the pre-rename legacy keys — see
		// SessionData's own doc comment. Never written on save, only read.
		// Deliberately left `undefined` (not defaulted to `[]`) when the saved
		// session has neither: every read site already does `tab.promptSegments
		// || []` (PromptSection.svelte, generationOrchestrator.ts), and
		// `collectCurrentSessionData()` serializes `tab.promptSegments`
		// unconditionally — defaulting to a real `[]` here would turn into a
		// phantom "unsaved changes" diff against any session saved before this
		// field existed, the same trap `variables` below is engineered to avoid.
		promptSegments: modeData.segments || modeData.promptSegments,
		negativePromptSegments: modeData.negativeSegments || modeData.negativePromptSegments,
		promptTabs: modeData.promptTabs,
		activePromptTab: modeData.activePromptTab,
		promptRelay: modeData.promptRelay,
		videoDirector: modeData.videoDirector,
		musicDirector: modeData.musicDirector,
		formData: modeData.formData || {},
		variables: modeData.variables || {},
		seed: modeData.seed,
		selectedBackendId: modeData.selectedBackendId || fallback.selectedBackendId || undefined,
		workbenchMaxHeight: modeData.workbenchMaxHeight || DEFAULT_WORKBENCH_MAX_HEIGHT,
		leftPanelWidth: modeData.leftPanelWidth || DEFAULT_LEFT_PANEL_WIDTH,
		...(modeData.layoutMode === 'two' || modeData.layoutMode === 'three'
			? { layoutMode: modeData.layoutMode }
			: {}),
		...(typeof modeData.leftPanelCollapsed === 'boolean'
			? { leftPanelCollapsed: modeData.leftPanelCollapsed }
			: {}),
		promptPanelWidth: modeData.promptPanelWidth || fallback.promptPanelWidth || DEFAULT_PROMPT_PANEL_WIDTH,
		positiveSegmentsCollapsed: modeData.positiveSegmentsCollapsed,
		negativeSegmentsCollapsed: modeData.negativeSegmentsCollapsed,
		// Omitted (not defaulted) when the saved session has none yet — leaves
		// the tab's own persisted sectionCollapsed map (if any) untouched rather
		// than stomping it with `{}`.
		...(modeData.sectionCollapsed ? { sectionCollapsed: modeData.sectionCollapsed } : {})
	};
}
