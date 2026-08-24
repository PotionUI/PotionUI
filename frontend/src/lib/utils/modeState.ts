// Per-mode scoping for the prompt/segment editor AND the dynamic form. A
// tab's `prompt`, `negativePrompt`, `promptSegments`, `negativePromptSegments`,
// `promptTabs`, `activePromptTab` and `formData` describe the ACTIVE mode
// only; content for every other mode the tab has visited lives in
// `Tab.modeStateByMode`, keyed by mode name. `buildModeSwitchPatch` is the
// single place that moves data between the two on a mode switch — snapshot
// the mode being left, restore (or default) the mode being entered — so
// every call site that changes `selectedMode` (the mode selector, the
// stale-mode fallback) stays in sync.
//
// `modeStateFromSessionData`/`mergeCachedModesIntoSessionData` are the
// session-side half of the same mechanism: a session's `ModeBasedSessionData`
// already keys prompt/form data by mode (has done since sessions existed),
// so loading one seeds this cache for every mode it covers, and saving one
// folds this cache's other-mode entries in alongside the active mode's live
// snapshot — a save captures every mode the tab actually visited, not just
// whichever one was active when Save was clicked. No Svelte imports.

import type { ModeState, Tab } from '$lib/types/tabs';
import type { ModeBasedSessionData, SessionData } from '$lib/types/api';

export function captureModeState(
	tab: Pick<Tab, 'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments' | 'promptTabs' | 'activePromptTab' | 'formData'>
): ModeState {
	return {
		prompt: tab.prompt,
		negativePrompt: tab.negativePrompt,
		promptSegments: tab.promptSegments || [],
		negativePromptSegments: tab.negativePromptSegments || [],
		promptTabs: tab.promptTabs,
		activePromptTab: tab.activePromptTab,
		formData: tab.formData || {}
	};
}

export function emptyModeState(): ModeState {
	return {
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {}
	};
}

/**
 * Builds the tab patch for a mode switch: the mode being left is snapshotted
 * into `modeStateByMode`, and the mode being entered is restored from there
 * — or starts empty (which `DynamicForm`'s own `mergeFormData(schemaDefaults,
 * initialData)` then fills out with the new mode's schema defaults, exactly
 * as it already does for a freshly-loaded session) when the tab has never
 * visited it. `fromMode` is `null` for a tab's very first mode selection
 * (nothing to snapshot yet). Applying the returned patch and
 * `selectedMode: toMode` in the SAME `updateTab` call keeps the switch
 * atomic — `formData`/`promptSegments` change in the same tick `mode` does,
 * so `DynamicForm` never renders one mode's schema against another's data.
 */
export function buildModeSwitchPatch(tab: Tab, fromMode: string | null, toMode: string): Partial<Tab> {
	if (fromMode === toMode) return {};

	const modeStateByMode = { ...(tab.modeStateByMode || {}) };
	if (fromMode) {
		modeStateByMode[fromMode] = captureModeState(tab);
	}
	const restored = modeStateByMode[toMode] || emptyModeState();

	return {
		modeStateByMode,
		prompt: restored.prompt,
		negativePrompt: restored.negativePrompt,
		promptSegments: restored.promptSegments,
		negativePromptSegments: restored.negativePromptSegments,
		promptTabs: restored.promptTabs,
		activePromptTab: restored.activePromptTab,
		formData: restored.formData
	};
}

/** Drops every cached mode's state — used when the preset itself changes,
 *  since mode names are only meaningful within their own preset and a new
 *  preset reusing a name (e.g. "video") must not inherit stale content. */
export function clearedModeStateByMode(): Record<string, ModeState> {
	return {};
}

/** The restore-direction conversion: a session's per-mode payload -> the
 *  cache shape. Tolerates the pre-rename `segments`/`negativeSegments` keys,
 *  same as `buildSessionRestoreTabPatch` does for the active mode. */
export function modeStateFromSessionData(data: SessionData): ModeState {
	return {
		prompt: data.prompt || '',
		negativePrompt: data.negativePrompt || '',
		promptSegments: data.segments || data.promptSegments || [],
		negativePromptSegments: data.negativeSegments || data.negativePromptSegments || [],
		promptTabs: data.promptTabs,
		activePromptTab: data.activePromptTab,
		formData: data.formData || {}
	};
}

/** Seeds `Tab.modeStateByMode` from a loaded session: every mode the session
 *  has data for becomes a cache entry, except `activeMode` — that mode's data
 *  is applied straight to the tab's live fields instead (see
 *  `buildSessionRestoreTabPatch`), not routed through the cache. Replaces
 *  whatever the tab's cache held before, the same way loading a session
 *  replaces the active mode's own fields wholesale. */
export function seedModeStateFromSessionData(
	modeBasedData: ModeBasedSessionData,
	activeMode: string | null
): Record<string, ModeState> {
	const seeded: Record<string, ModeState> = {};
	for (const [mode, data] of Object.entries(modeBasedData)) {
		if (mode === activeMode) continue;
		seeded[mode] = modeStateFromSessionData(data);
	}
	return seeded;
}

/**
 * The capture-direction counterpart: folds every mode cached in
 * `modeStateByMode` into `baseline` (typically the currently-saved session's
 * `.data`), so a save request carries every mode the tab has visited, not
 * just whichever one is active. Fields the cache doesn't track for a mode
 * (seed, backend, layout, …) are left exactly as `baseline` had them — the
 * cache only ever overlays the prompt/segment/form fields it knows about.
 * The active mode itself is never in `modeStateByMode` (see
 * `buildModeSwitchPatch`), so callers still need to set `result[activeMode]`
 * to that mode's fresh live snapshot themselves.
 */
export function mergeCachedModesIntoSessionData(
	baseline: ModeBasedSessionData,
	modeStateByMode: Record<string, ModeState> | undefined
): ModeBasedSessionData {
	const merged: ModeBasedSessionData = { ...baseline };
	for (const [mode, state] of Object.entries(modeStateByMode || {})) {
		merged[mode] = {
			...(merged[mode] || {}),
			prompt: state.prompt,
			negativePrompt: state.negativePrompt,
			promptSegments: state.promptSegments,
			negativePromptSegments: state.negativePromptSegments,
			promptTabs: state.promptTabs,
			activePromptTab: state.activePromptTab,
			formData: state.formData
		};
	}
	return merged;
}
