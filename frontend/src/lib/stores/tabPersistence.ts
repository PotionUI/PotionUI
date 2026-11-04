import { browser } from '$app/environment';
import type { GenerationTab } from '$lib/types/tabs';
import type { PersistedGenerationTab, PersistedTab, PersistedTabsState, PersistedToolTab, Tab, ToolTab } from '$lib/types/tabs';
import { isToolTab } from '$lib/types/tabs';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { scopedStorage } from '$lib/stores/identityScopedStorage';
import { randomUUID } from '$lib/utils/uuid';
import { tabDraftOutbox } from '$lib/stores/tabDraftOutbox';

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

function toPersistedToolTab(t: ToolTab): PersistedToolTab {
	const thumbnail = t.thumbnail && !DATA_URI_RE.test(t.thumbnail) && !t.thumbnail.startsWith('blob:') ? t.thumbnail : null;
	return {
		kind: 'tool',
		id: t.id,
		name: t.name,
		color: t.color ?? null,
		tool: t.tool,
		source: t.source,
		state: sanitizeForPersistence(t.state ?? null),
		run: t.run ?? null,
		activeTool: t.activeTool ?? null,
		dirty: t.dirty ?? 0,
		thumbnail,
		renamed: t.renamed ?? false,
		key: t.key ?? null,
		status: t.status ?? null
	};
}

export function toPersistedTab(t: GenerationTab): PersistedGenerationTab;
export function toPersistedTab(t: ToolTab): PersistedToolTab;
export function toPersistedTab(t: Tab): PersistedTab;
export function toPersistedTab(t: Tab): PersistedTab {
	if (isToolTab(t)) return toPersistedToolTab(t);
	return {
		kind: 'generation',
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
		promptPanelWidthBeforeDirector: t.promptPanelWidthBeforeDirector,
		promptPanelWidthFolded: t.promptPanelWidthFolded,
		positiveSegmentsCollapsed: t.positiveSegmentsCollapsed,
		negativeSegmentsCollapsed: t.negativeSegmentsCollapsed,
		sectionCollapsed: t.sectionCollapsed,
		compare: t.compare ? (sanitizeForPersistence(t.compare) as GenerationTab['compare']) : undefined,
		compareGridId: t.compareGridId ?? null,
		workbenchMaxHeight: t.workbenchMaxHeight,
		workbenchFloatingWidth: t.workbenchFloatingWidth,
		leftPanelWidth: t.leftPanelWidth,
		leftPanelCollapsed: t.leftPanelCollapsed,
		workbenchCollapsed: t.workbenchCollapsed,
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
		directorRuns: t.directorRuns,
		directorRunLinks: t.directorRunLinks,
		formData: sanitizeForPersistence(t.formData) as Record<string, unknown>,
		modeStateByMode: sanitizeForPersistence(t.modeStateByMode) as GenerationTab['modeStateByMode'],
		seed: t.seed
	};
}

export function saveTabsToLocalStorage(tabs: Tab[], activeTabId: string, lastGenerationTabId?: string): void {
	if (!browser) return;
	try {
		const persisted: PersistedTabsState = {
			tabs: tabs.map((tab) => {
				if (isToolTab(tab)) return toPersistedTab(tab);
				const persistedTab = toPersistedTab(tab);
				return tabDraftOutbox.retainedLegacyIds().has(tab.id)
					? { ...persistedTab, variables: tab.variables }
					: persistedTab;
			}),
			activeTabId,
			...(lastGenerationTabId ? { lastGenerationTabId } : {})
		};
		scopedStorage.set(TABS_STORAGE_KEY, JSON.stringify(persisted));
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

	return { state: { ...parsed, tabs, activeTabId }, changed: true };
}

export function loadTabsFromLocalStorage(): PersistedTabsState | null {
	if (!browser) return null;
	try {
		const raw = scopedStorage.get(TABS_STORAGE_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as PersistedTabsState;
		if (!parsed.tabs || !Array.isArray(parsed.tabs) || parsed.tabs.length === 0) return null;

		const { state, changed } = migrateLegacyTabIds(parsed);
		if (changed) {
			try {
				scopedStorage.set(TABS_STORAGE_KEY, JSON.stringify(state));
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
