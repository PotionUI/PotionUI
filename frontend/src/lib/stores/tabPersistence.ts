import { browser } from '$app/environment';
import type { Tab } from '$lib/types/tabs';
import type { PersistedTab, PersistedTabsState } from '$lib/types/tabs';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { randomUUID } from '$lib/utils/uuid';

// A field value that slipped a `data:` URI into `formData`/`variables` (an
// inline image/audio blob, e.g. a mask preview) would otherwise get written
// to localStorage verbatim on every keystroke — walk the value and drop any
// string that looks like one, keeping everything else (including ordinary
// media references like `{ path, relative_path, url }`) untouched.
const DATA_URI_RE = /^data:[^,]*,/;

function sanitizeForPersistence(value: unknown): unknown {
	if (typeof value === 'string') {
		return DATA_URI_RE.test(value) ? null : value;
	}
	if (Array.isArray(value)) {
		return value.map(sanitizeForPersistence);
	}
	if (value && typeof value === 'object') {
		const result: Record<string, unknown> = {};
		for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
			result[key] = sanitizeForPersistence(entry);
		}
		return result;
	}
	return value;
}

/** Kept pure so persistence omissions are covered without a browser. */
export function toPersistedTab(t: Tab): PersistedTab {
	return {
		id: t.id,
		name: t.name,
		selectedPreset: t.selectedPreset,
		selectedMode: t.selectedMode,
		selectedVariant: t.selectedVariant ?? null,
		selectedSessionId: t.selectedSessionId ?? null,
		activeGenerationId: t.activeGenerationId ?? null,
		queuedGenerationIds: t.generation.queue?.map((q) => q.generation_id) ?? [],
		autoTagIds: t.autoTagIds,
		autoCollectionIds: t.autoCollectionIds,
		soundOnComplete: t.soundOnComplete,
		soundOnError: t.soundOnError,
		color: t.color ?? null,
		layoutMode: t.layoutMode,
		promptPanelWidth: t.promptPanelWidth,
		positiveSegmentsCollapsed: t.positiveSegmentsCollapsed,
		negativeSegmentsCollapsed: t.negativeSegmentsCollapsed,
		sectionCollapsed: t.sectionCollapsed,
		workbenchMaxHeight: t.workbenchMaxHeight,
		leftPanelWidth: t.leftPanelWidth,
		leftPanelCollapsed: t.leftPanelCollapsed,
		// Unsaved-tab content — a tab bound to a saved session
		// (`selectedSessionId` set) still gets these overwritten from the
		// server on load (`restoreTabSessions`), so this is only
		// load-bearing for a tab that was never saved.
		prompt: t.prompt,
		negativePrompt: t.negativePrompt,
		promptSegments: t.promptSegments,
		negativePromptSegments: t.negativePromptSegments,
		promptTabs: t.promptTabs,
		activePromptTab: t.activePromptTab,
		promptRelay: t.promptRelay,
		videoDirector: t.videoDirector,
		formData: sanitizeForPersistence(t.formData) as Record<string, unknown>,
		variables: sanitizeForPersistence(t.variables) as Tab['variables'],
		modeStateByMode: sanitizeForPersistence(t.modeStateByMode) as Tab['modeStateByMode'],
		seed: t.seed,
		selectedBackendId: t.selectedBackendId ?? null
	};
}

export function saveTabsToLocalStorage(tabs: Tab[], activeTabId: string): void {
	if (!browser) return;
	try {
		const persisted: PersistedTabsState = {
			tabs: tabs.map(toPersistedTab),
			activeTabId
		};
		localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify(persisted));
	} catch {
		// localStorage may be full or unavailable
	}
}

// Legacy tab ids looked like `tab-1` or `tab-${Date.now()}-${seq}` — both always
// start with "tab-". Current ids are crypto.randomUUID() values, which never
// do. Two browsers logged in as the same user could both end up with a tab
// literally named `tab-1`, and the backend queue routes queued generations
// back to the tab id that enqueued them — so a collision would cross-route
// results between browsers. This migration runs once per legacy payload and
// rewrites every legacy id to a fresh, globally-unique one while preserving
// every other field (including which tab was active).
const LEGACY_TAB_ID_RE = /^tab-/;

function isLegacyTabId(id: string): boolean {
	return LEGACY_TAB_ID_RE.test(id);
}

function migrateLegacyTabIds(parsed: PersistedTabsState): { state: PersistedTabsState; changed: boolean } {
	const idMap = new Map<string, string>();
	for (const tab of parsed.tabs) {
		if (isLegacyTabId(tab.id)) {
			idMap.set(tab.id, randomUUID());
		}
	}
	if (idMap.size === 0) {
		return { state: parsed, changed: false };
	}

	const tabs = parsed.tabs.map((tab) => ({
		...tab,
		id: idMap.get(tab.id) ?? tab.id
	}));
	const activeTabId = idMap.get(parsed.activeTabId) ?? parsed.activeTabId;

	return { state: { tabs, activeTabId }, changed: true };
}

export function loadTabsFromLocalStorage(): PersistedTabsState | null {
	if (!browser) return null;
	try {
		const raw = localStorage.getItem(TABS_STORAGE_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as PersistedTabsState;
		if (!parsed.tabs || !Array.isArray(parsed.tabs) || parsed.tabs.length === 0) return null;

		const { state, changed } = migrateLegacyTabIds(parsed);
		if (changed) {
			try {
				localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify(state));
			} catch {
				// localStorage may be full or unavailable — the in-memory migrated
				// state is still returned below, it just won't stick across reloads.
			}
		}
		return state;
	} catch {
		return null;
	}
}

// Exported for unit testing the one-time migration in isolation.
export { migrateLegacyTabIds, isLegacyTabId };

export function debounce<T extends (...args: any[]) => void>(fn: T, ms: number): T {
	let timer: ReturnType<typeof setTimeout>;
	return ((...args: any[]) => {
		clearTimeout(timer);
		timer = setTimeout(() => fn(...args), ms);
	}) as T;
}
