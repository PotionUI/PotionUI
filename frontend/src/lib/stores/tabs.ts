import { writable, derived, type Readable } from 'svelte/store';
import type { Tab, GenerationState } from '$lib/types/tabs';
import { saveTabsToLocalStorage, loadTabsFromLocalStorage, debounce } from './tabPersistence';
import { DEFAULT_PROMPT_PANEL_WIDTH } from './generationLayout';
import { randomUUID } from '$lib/utils/uuid';
import { getGlobalSoundDefault } from '$lib/utils/soundSettings';
import { tabClaimedGenerationIds } from '$lib/generation/messages/ownership';
import { retireOrphanedGenerationIds } from '$lib/generation/messages/generationOutputs';

function createInitialGenerationState(): GenerationState {
	return {
		isGenerating: false,
		currentGeneration: null,
		currentProgress: null,
		pipeTimers: {},
		startedAt: null,
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		batchMeshes: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null
	};
}

// Tab ids must be globally unique across browsers/devices logged in as the same
// user — the backend queue routes queued generations back to the tab that
// enqueued them, so two tabs sharing an id would cross-route results. Uses the
// shared randomUUID helper (NOT bare crypto.randomUUID(), which is undefined
// outside secure contexts — plain-http LAN access crashed here) rather than a
// counter-based scheme like the legacy `tab-${Date.now()}-${seq}` ids.
function generateTabId(): string {
	return randomUUID();
}

function createDefaultTab(id: string, name: string): Tab {
	return {
		id,
		name,
		selectedPreset: null,
		selectedMode: null,
		selectedVariant: null,
		selectedSessionId: null,
		activeGenerationId: null,
		prompt: '',
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {},
		variables: {},
		generation: createInitialGenerationState(),
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		leftPanelCollapsed: false,
		workbenchCollapsed: false,
		layoutMode: 'two',
		promptPanelWidth: DEFAULT_PROMPT_PANEL_WIDTH,
		positiveSegmentsCollapsed: undefined,
		negativeSegmentsCollapsed: undefined,
		sectionCollapsed: undefined,
		autoTagIds: [],
		autoCollectionIds: [],
		soundOnComplete: getGlobalSoundDefault('complete'),
		soundOnError: getGlobalSoundDefault('error'),
		color: null
	};
}

interface TabsState {
	tabs: Tab[];
	activeTabId: string;
}

function createEqualityAwareStore(initialValue: TabsState) {
	let current = initialValue;
	const base = writable(initialValue);

	function set(newValue: TabsState) {
		if (newValue === current) return;
		current = newValue;
		base.set(newValue);
	}

	function update(fn: (value: TabsState) => TabsState) {
		set(fn(current));
	}

	return { set, update, subscribe: base.subscribe };
}

function tabPersistenceFieldsChanged(a: Tab, b: Tab): boolean {
	for (const key of new Set([...Object.keys(a), ...Object.keys(b)]) as Set<keyof Tab>) {
		if (key === 'generation') continue;
		if (a[key] !== b[key]) return true;
	}
	return a.generation.queue !== b.generation.queue;
}

function tabsRelevantToPersistenceChanged(prev: TabsState, next: TabsState): boolean {
	if (prev.activeTabId !== next.activeTabId) return true;
	if (prev.tabs.length !== next.tabs.length) return true;
	for (let i = 0; i < next.tabs.length; i++) {
		const prevTab = prev.tabs[i];
		const nextTab = next.tabs[i];
		if (prevTab === nextTab) continue;
		if (prevTab.id !== nextTab.id) return true;
		if (tabPersistenceFieldsChanged(prevTab, nextTab)) return true;
	}
	return false;
}

function createTabsStore() {
	// Try to load from localStorage, fallback to default tab
	const persisted = loadTabsFromLocalStorage();
	// Older builds could persist duplicate ids (same-millisecond Date.now());
	// regenerate any colliding id so keyed each blocks never see duplicates.
	const seenIds = new Set<string>();
	const initialTabs: Tab[] = persisted
		? persisted.tabs.map(p => {
				const id = seenIds.has(p.id) ? generateTabId() : p.id;
				seenIds.add(id);
				return {
					...createDefaultTab(id, p.name),
					selectedPreset: p.selectedPreset,
					selectedMode: p.selectedMode,
					selectedVariant: p.selectedVariant ?? null,
					selectedSessionId: p.selectedSessionId,
					activeGenerationId: p.activeGenerationId || null,
					autoTagIds: p.autoTagIds || [],
					autoCollectionIds: p.autoCollectionIds || [],
					soundOnComplete: p.soundOnComplete ?? getGlobalSoundDefault('complete'),
					soundOnError: p.soundOnError ?? getGlobalSoundDefault('error'),
					color: p.color ?? null,
					layoutMode: p.layoutMode === 'three' ? 'three' : 'two',
					promptPanelWidth: p.promptPanelWidth || DEFAULT_PROMPT_PANEL_WIDTH,
					promptPanelWidthBeforeDirector: p.promptPanelWidthBeforeDirector,
					positiveSegmentsCollapsed: p.positiveSegmentsCollapsed,
					negativeSegmentsCollapsed: p.negativeSegmentsCollapsed,
					sectionCollapsed: p.sectionCollapsed,
					workbenchMaxHeight: p.workbenchMaxHeight || '600',
					workbenchFloatingWidth: p.workbenchFloatingWidth,
					leftPanelWidth: p.leftPanelWidth || 380,
					leftPanelCollapsed: p.leftPanelCollapsed ?? false,
					workbenchCollapsed: p.workbenchCollapsed ?? false,
					// Unsaved-tab content — overwritten by restoreTabSessions
					// on mount when this tab has a selectedSessionId; otherwise this
					// is the only source for it.
					prompt: p.prompt ?? '',
					negativePrompt: p.negativePrompt ?? '',
					promptSegments: p.promptSegments ?? [],
					negativePromptSegments: p.negativePromptSegments ?? [],
					promptTabs: p.promptTabs,
					activePromptTab: p.activePromptTab,
					promptRelay: p.promptRelay,
					videoDirector: p.videoDirector,
					directorRuns: p.directorRuns,
					directorRunLinks: p.directorRunLinks,
					musicDirector: p.musicDirector,
					formData: p.formData ?? {},
					variables: p.variables ?? {},
					modeStateByMode: p.modeStateByMode ?? {},
					seed: p.seed
				};
		  })
		: [createDefaultTab(generateTabId(), 'Generation 1')];
	let initialActiveTabId = persisted?.activeTabId || initialTabs[0].id;
	if (!initialTabs.some((t) => t.id === initialActiveTabId)) {
		initialActiveTabId = initialTabs[0].id;
	}

	const { subscribe, set, update } = createEqualityAwareStore({
		tabs: initialTabs,
		activeTabId: initialActiveTabId
	});

	// Debounced save to localStorage (500ms delay)
	const debouncedSave = debounce((tabs: Tab[], activeTabId: string) => {
		saveTabsToLocalStorage(tabs, activeTabId);
	}, 500);

	// Subscribe to changes and persist to localStorage
	let lastPersistedState: TabsState | null = null;
	subscribe((state) => {
		if (lastPersistedState && !tabsRelevantToPersistenceChanged(lastPersistedState, state)) return;
		lastPersistedState = state;
		debouncedSave(state.tabs, state.activeTabId);
	});

	return {
		subscribe,

		// Tab management
		addTab: () => {
			update((state) => {
				const newId = generateTabId();
				const newTab = createDefaultTab(newId, `Generation ${state.tabs.length + 1}`);
				return {
					...state,
					tabs: [...state.tabs, newTab],
					activeTabId: newId
				};
			});
		},

		addTabWithData: (name: string, data: Partial<Tab>): string => {
			const newId = generateTabId();
			update((state) => {
				const newTab = { ...createDefaultTab(newId, name), ...data };
				return {
					...state,
					tabs: [...state.tabs, newTab],
					activeTabId: newId
				};
			});
			return newId;
		},

		removeTab: (tabId: string) => {
			update((state) => {
				const closedTab = state.tabs.find((t) => t.id === tabId);
				const newTabs = state.tabs.filter((t) => t.id !== tabId);
				if (newTabs.length === 0) {
					// Always keep at least one tab
					return state;
				}

				// Consumer-tied generation resource lifetime: a generation this
				// closed tab was the LAST consumer of (nothing else claims it --
				// see tabClaimedGenerationIds) loses its cached outputs and
				// WebSocket subscription now; one a still-open tab also claims
				// (a shared Director run, say) keeps them untouched.
				if (closedTab) {
					const claimed = tabClaimedGenerationIds(closedTab);
					if (claimed.size > 0) {
						const stillClaimed = new Set<string>();
						for (const tab of newTabs) {
							for (const id of tabClaimedGenerationIds(tab)) stillClaimed.add(id);
						}
						const orphaned = [...claimed].filter((id) => !stillClaimed.has(id));
						if (orphaned.length > 0) retireOrphanedGenerationIds(orphaned);
					}
				}

				let newActiveId = state.activeTabId;
				if (state.activeTabId === tabId) {
					// Switch to the first tab if we're closing the active one
					newActiveId = newTabs[0].id;
				}

				return {
					...state,
					tabs: newTabs,
					activeTabId: newActiveId
				};
			});
		},

		setActiveTab: (tabId: string) => {
			update((state) => ({ ...state, activeTabId: tabId }));
		},

		// Tab updates
		updateTab: (tabId: string, updates: Partial<Tab>) => {
			update((state) => {
				const index = state.tabs.findIndex((tab) => tab.id === tabId);
				if (index === -1) return state;

				const current = state.tabs[index];
				const generationChanged = updates.generation
					? Object.entries(updates.generation).some(
							([key, value]) => !Object.is(current.generation[key as keyof GenerationState], value)
						)
					: false;
				const tabChanged = Object.entries(updates).some(
					([key, value]) => key !== 'generation' && !Object.is(current[key as keyof Tab], value)
				);
				if (!tabChanged && !generationChanged) return state;

				const nextTab: Tab = {
					...current,
					...updates,
					generation: updates.generation
						? { ...current.generation, ...updates.generation }
						: current.generation
				};
				const tabs = [...state.tabs];
				tabs[index] = nextTab;
				return { ...state, tabs };
			});
		},

		// Patches every open tab with the same fields — used by the "apply to
		// all tabs" generation-sound preference to propagate a toggle change
		// beyond the tab it was changed in.
		updateAllTabs: (updates: Partial<Tab>) => {
			update((state) => ({
				...state,
				tabs: state.tabs.map((tab) => ({ ...tab, ...updates }))
			}));
		},

		renameTab: (tabId: string, name: string) => {
			update((state) => ({
				...state,
				tabs: state.tabs.map((tab) => (tab.id === tabId ? { ...tab, name } : tab))
			}));
		},

		reorderTabs: (draggedId: string, targetId: string, position: 'left' | 'right') => {
			update((state) => {
				const tabs = [...state.tabs];
				const draggedIndex = tabs.findIndex((t) => t.id === draggedId);
				const targetIndex = tabs.findIndex((t) => t.id === targetId);

				if (draggedIndex === -1 || targetIndex === -1) return state;

				// Remove dragged tab
				const [draggedTab] = tabs.splice(draggedIndex, 1);

				// Calculate new index
				let newIndex = targetIndex;
				if (draggedIndex < targetIndex) {
					// Dragging forward
					newIndex = position === 'left' ? targetIndex - 1 : targetIndex;
				} else {
					// Dragging backward
					newIndex = position === 'left' ? targetIndex : targetIndex + 1;
				}

				// Insert at new position
				tabs.splice(newIndex, 0, draggedTab);

				return {
					...state,
					tabs
				};
			});
		},

		// Bulk operations
		reset: () => {
			const id = generateTabId();
			set({
				tabs: [createDefaultTab(id, 'Generation 1')],
				activeTabId: id
			});
		}
	};
}

export const tabsStore = createTabsStore();

function selectorStore<T>(selector: (state: TabsState) => T): Readable<T> {
	let last: T | undefined;
	return derived(tabsStore, ($store, set) => {
		const next = selector($store);
		if (next === last) return;
		last = next;
		set(next);
	});
}

// Derived stores for convenience
export const activeTab = selectorStore(
	($store) => $store.tabs.find((t) => t.id === $store.activeTabId) || $store.tabs[0]
);

// The generation queue lets many tabs have in-flight work simultaneously, so
// there is no longer a single exclusive "generating tab" lock (the old
// `generatingTabId` field). These are derived straight from each tab's own
// `generation.isGenerating` flag instead of a separately-tracked id, so they
// can never point at a tab that finished (or drop a tab that started) due to
// a handler forgetting to clear/set a shared lock.
export const generatingTab = selectorStore(($store) => $store.tabs.find((t) => t.generation.isGenerating) || null);

export const isAnyTabGenerating = derived(tabsStore, ($store) => {
	return $store.tabs.some((t) => t.generation.isGenerating);
});

export const isActiveTabGenerating = derived(activeTab, ($activeTab) => {
	return !!$activeTab?.generation.isGenerating;
});
