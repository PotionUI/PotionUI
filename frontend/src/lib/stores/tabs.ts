import { writable, derived, type Readable } from 'svelte/store';
import type { GenerationTab, GenerationState, MediaRef, PersistedGenerationTab, PersistedToolTab, Tab, ToolTab, ToolTabHint } from '$lib/types/tabs';
import { isGenerationTab, isToolTab } from '$lib/types/tabs';
import { findToolTabFor, mediaFileStem, planToolTabOpen } from '$lib/tool-tabs/sources';
import { toolLabel } from '$lib/tool-tabs/registry';
import { registerBuiltinToolKinds } from '$lib/tool-tabs/builtin';

registerBuiltinToolKinds();
import { saveTabsToLocalStorage, loadTabsFromLocalStorage } from './tabPersistence';
import { debounce } from '$lib/utils/time';
import { DEFAULT_PROMPT_PANEL_WIDTH } from './generationLayout';
import { randomUUID } from '$lib/utils/uuid';
import { getGlobalSoundDefault } from '$lib/utils/soundSettings';
import { tabClaimedGenerationIds } from '$lib/generation/messages/ownership';
import { retireOrphanedGenerationIds } from '$lib/generation/messages/generationOutputs';
import { formulaApplied } from './formulaApplied';
import { mirrorActivePrompt } from '$lib/generation/promptBatch';
import { seedPromptTabs, seedSegmentsFromText } from '$lib/utils/promptTextSeed';
import { adoptLegacyVariables } from './tabDraftOutbox';

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

function createDefaultTab(id: string, name: string): GenerationTab {
	return {
		kind: 'generation',
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
		promptPanelWidthFolded: undefined,
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

function createToolTab(id: string, tool: string, source: MediaRef | null, name: string, activeTool: string | null): ToolTab {
	return {
		kind: 'tool',
		id,
		name: mediaFileStem(source) ?? name,
		color: null,
		tool,
		source,
		state: null,
		run: null,
		activeTool,
		dirty: 0,
		thumbnail: null,
		renamed: false
	};
}

function restoreToolTab(id: string, p: PersistedToolTab): ToolTab {
	return {
		kind: 'tool',
		id,
		name: p.name,
		color: p.color ?? null,
		tool: p.tool,
		source: p.source ?? null,
		state: p.state ?? null,
		run: p.run ?? null,
		activeTool: p.activeTool ?? null,
		dirty: p.dirty ?? 0,
		thumbnail: p.thumbnail ?? null,
		renamed: p.renamed ?? false
	};
}

export interface OpenToolTabOptions {
	activeTool?: string | null;
	hint?: ToolTabHint | null;
	activate?: boolean;
}

export type ToolTabUpdate = Partial<Omit<ToolTab, 'kind' | 'id'>>;

const TAB_BASE_KEYS = ['name', 'color'] as const;

export interface TabsState {
	tabs: Tab[];
	activeTabId: string;
	lastGenerationTabId: string;
}

export function normalizeTabsState(state: TabsState): TabsState {
	let tabs = state.tabs;
	if (!tabs.some(isGenerationTab)) {
		tabs = [...tabs, createDefaultTab(generateTabId(), 'Generation 1')];
	}
	const activeTabId = tabs.some((tab) => tab.id === state.activeTabId) ? state.activeTabId : tabs[0].id;
	const active = tabs.find((tab) => tab.id === activeTabId);
	const last = tabs.find((tab) => tab.id === state.lastGenerationTabId);
	const lastGenerationTabId = isGenerationTab(active)
		? active.id
		: isGenerationTab(last)
			? last.id
			: tabs.find(isGenerationTab)!.id;
	if (tabs === state.tabs && activeTabId === state.activeTabId && lastGenerationTabId === state.lastGenerationTabId) {
		return state;
	}
	return { ...state, tabs, activeTabId, lastGenerationTabId };
}

function appendToolTab(state: TabsState, data: ToolTabUpdate & { tool: string }, activate: boolean): { state: TabsState; id: string } {
	const id = generateTabId();
	const source = data.source ?? null;
	const tab: ToolTab = {
		...createToolTab(id, data.tool, source, toolLabel(data.tool), data.activeTool ?? null),
		...data,
		kind: 'tool',
		id
	};
	return { state: { ...state, tabs: [...state.tabs, tab], activeTabId: activate ? id : state.activeTabId }, id };
}

function createEqualityAwareStore(initialValue: TabsState) {
	let current = normalizeTabsState(initialValue);
	const base = writable(current);

	function set(next: TabsState) {
		const newValue = normalizeTabsState(next);
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
	if (a.kind !== b.kind) return true;
	const left = a as unknown as Record<string, unknown>;
	const right = b as unknown as Record<string, unknown>;
	for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
		if (key === 'generation') continue;
		if (left[key] !== right[key]) return true;
	}
	if (isGenerationTab(a) && isGenerationTab(b)) return a.generation.queue !== b.generation.queue;
	return false;
}

function tabsRelevantToPersistenceChanged(prev: TabsState, next: TabsState): boolean {
	if (prev.activeTabId !== next.activeTabId) return true;
	if (prev.lastGenerationTabId !== next.lastGenerationTabId) return true;
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

const PROMPT_UPDATE_KEYS = ['prompt', 'negativePrompt', 'promptSegments', 'negativePromptSegments', 'promptTabs'] as const;

function seedTabPrompts(tab: GenerationTab): GenerationTab {
	return { ...seedSegmentsFromText(tab), promptTabs: seedPromptTabs(tab.promptTabs) };
}

function createTabsStore() {
	// Try to load from localStorage, fallback to default tab
	const persisted = loadTabsFromLocalStorage();
	const legacyVariables = persisted ? adoptLegacyVariables(persisted.tabs) : {};
	// Older builds could persist duplicate ids (same-millisecond Date.now());
	// regenerate any colliding id so keyed each blocks never see duplicates.
	const seenIds = new Set<string>();
	const restoreGenerationTab = (id: string, p: PersistedGenerationTab): GenerationTab =>
		seedTabPrompts({
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
					promptPanelWidthFolded: p.promptPanelWidthFolded,
					positiveSegmentsCollapsed: p.positiveSegmentsCollapsed,
					negativeSegmentsCollapsed: p.negativeSegmentsCollapsed,
					sectionCollapsed: p.sectionCollapsed,
					compare: p.compare,
					compareGridId: p.compareGridId ?? null,
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
					variables: legacyVariables[p.id] ?? {},
					modeStateByMode: p.modeStateByMode ?? {},
					seed: p.seed
				});
	const initialTabs: Tab[] = persisted
		? persisted.tabs.map((p) => {
				const id = seenIds.has(p.id) ? generateTabId() : p.id;
				seenIds.add(id);
				return p.kind === 'tool' ? restoreToolTab(id, p) : restoreGenerationTab(id, p);
		  })
		: [createDefaultTab(generateTabId(), 'Generation 1')];
	let initialActiveTabId = persisted?.activeTabId || initialTabs[0].id;
	if (!initialTabs.some((t) => t.id === initialActiveTabId)) {
		initialActiveTabId = initialTabs[0].id;
	}

	const { subscribe, set, update } = createEqualityAwareStore({
		tabs: initialTabs,
		activeTabId: initialActiveTabId,
		lastGenerationTabId: persisted?.lastGenerationTabId ?? ''
	});

	// Debounced save to localStorage (500ms delay)
	let savePending = false;
	const debouncedSave = debounce((state: TabsState) => {
		savePending = false;
		saveTabsToLocalStorage(state.tabs, state.activeTabId, state.lastGenerationTabId);
	}, 500);

	function flushPendingSave() {
		if (!savePending) return;
		savePending = false;
		debouncedSave.cancel();
		subscribe((state) => saveTabsToLocalStorage(state.tabs, state.activeTabId, state.lastGenerationTabId))();
	}

	if (typeof window !== 'undefined' && typeof document !== 'undefined') {
		window.addEventListener('pagehide', flushPendingSave);
		document.addEventListener('visibilitychange', () => {
			if (document.visibilityState === 'hidden') flushPendingSave();
		});
	}

	// Subscribe to changes and persist to localStorage
	let lastPersistedState: TabsState | null = null;
	subscribe((state) => {
		if (lastPersistedState && !tabsRelevantToPersistenceChanged(lastPersistedState, state)) return;
		lastPersistedState = state;
		savePending = true;
		debouncedSave(state);
	});

	const tabsStoreApi = {
		subscribe,

		persistNow: () => {
			subscribe((state) => saveTabsToLocalStorage(state.tabs, state.activeTabId, state.lastGenerationTabId))();
		},

		// Tab management
		addTab: () => {
			update((state) => {
				const newId = generateTabId();
				const newTab = createDefaultTab(newId, `Generation ${state.tabs.filter(isGenerationTab).length + 1}`);
				return {
					...state,
					tabs: [...state.tabs, newTab],
					activeTabId: newId
				};
			});
		},

		addTabWithData: (name: string, data: Partial<Omit<GenerationTab, 'kind'>>): string => {
			const newId = generateTabId();
			update((state) => {
				const newTab: GenerationTab = { ...createDefaultTab(newId, name), ...data, kind: 'generation' };
				return {
					...state,
					tabs: [...state.tabs, newTab],
					activeTabId: newId
				};
			});
			return newId;
		},

		removeTab: (tabId: string) => {
			formulaApplied.clear(tabId);
			update((state) => {
				const closedTab = state.tabs.find((t) => t.id === tabId);
				const newTabs = state.tabs.filter((t) => t.id !== tabId);
				if (newTabs.length === 0) {
					return state;
				}

				// Consumer-tied generation resource lifetime: a generation this
				// closed tab was the LAST consumer of (nothing else claims it --
				// see tabClaimedGenerationIds) loses its cached outputs and
				// WebSocket subscription now; one a still-open tab also claims
				// (a shared Director run, say) keeps them untouched.
				if (isGenerationTab(closedTab)) {
					const claimed = tabClaimedGenerationIds(closedTab);
					if (claimed.size > 0) {
						const stillClaimed = new Set<string>();
						for (const tab of newTabs.filter(isGenerationTab)) {
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
			update((state) => (state.tabs.some((tab) => tab.id === tabId) ? { ...state, activeTabId: tabId } : state));
		},

		// Tab updates
		updateTab: (tabId: string, updates: Partial<Omit<GenerationTab, 'kind'>>) => {
			update((state) => {
				const index = state.tabs.findIndex((tab) => tab.id === tabId);
				if (index === -1) return state;

				const target = state.tabs[index];
				if (isToolTab(target)) {
					const baseUpdates: Partial<ToolTab> = {};
					for (const key of TAB_BASE_KEYS) {
						if (key in updates && !Object.is(target[key], updates[key])) {
							Object.assign(baseUpdates, { [key]: updates[key] });
						}
					}
					if (Object.keys(baseUpdates).length === 0) return state;
					const tabs = [...state.tabs];
					tabs[index] = { ...target, ...baseUpdates };
					return { ...state, tabs };
				}
				const current = target;
				const generationChanged = updates.generation
					? Object.entries(updates.generation).some(
							([key, value]) => !Object.is(current.generation[key as keyof GenerationState], value)
						)
					: false;
				const tabChanged = Object.entries(updates).some(
					([key, value]) => key !== 'generation' && !Object.is(current[key as keyof GenerationTab], value)
				);
				if (!tabChanged && !generationChanged) return state;

				const schemaChanged =
					!('formPublished' in updates) &&
					(['selectedPreset', 'selectedMode', 'selectedVariant'] as const).some(
						(key) => key in updates && !Object.is(current[key], updates[key])
					);
				const merged: GenerationTab = {
					...current,
					...updates,
					...(schemaChanged ? { formPublished: false, formBaselineSignature: undefined } : {}),
					generation: updates.generation
						? { ...current.generation, ...updates.generation }
						: current.generation
				};
				const touchesPrompt = PROMPT_UPDATE_KEYS.some((key) => key in updates);
				const seeded: GenerationTab = touchesPrompt
					? seedTabPrompts(merged)
					: merged;
				const nextTab: GenerationTab = { ...seeded, ...mirrorActivePrompt(seeded, updates) };
				const tabs = [...state.tabs];
				tabs[index] = nextTab;
				return { ...state, tabs };
			});
		},

		// Patches every open tab with the same fields — used by the "apply to
		// all tabs" generation-sound preference to propagate a toggle change
		// beyond the tab it was changed in.
		updateAllTabs: (updates: Partial<Omit<GenerationTab, 'kind'>>) => {
			update((state) => ({
				...state,
				tabs: state.tabs.map((tab) => (isGenerationTab(tab) ? { ...tab, ...updates } : tab))
			}));
		},

		addToolTabWithData: (data: ToolTabUpdate & { tool: string }): string => {
			let newId = '';
			update((state) => {
				const appended = appendToolTab(state, data, true);
				newId = appended.id;
				return appended.state;
			});
			return newId;
		},

		updateToolTab: (tabId: string, updates: ToolTabUpdate) => {
			update((state) => {
				const index = state.tabs.findIndex((tab) => tab.id === tabId);
				const current = state.tabs[index];
				if (!isToolTab(current)) return state;
				const changed = Object.entries(updates).some(
					([key, value]) => !Object.is((current as unknown as Record<string, unknown>)[key], value)
				);
				if (!changed) return state;
				const tabs = [...state.tabs];
				tabs[index] = { ...current, ...updates };
				return { ...state, tabs };
			});
		},

		setToolTabTitle: (tabId: string, name: string) => {
			update((state) => {
				const index = state.tabs.findIndex((tab) => tab.id === tabId);
				const current = state.tabs[index];
				if (!isToolTab(current) || current.renamed || current.name === name || !name) return state;
				const tabs = [...state.tabs];
				tabs[index] = { ...current, name };
				return { ...state, tabs };
			});
		},

		openToolTab: (tool: string, source: MediaRef | null, options: OpenToolTabOptions = {}): string => {
			let openedId = '';
			update((state) => {
				const plan = planToolTabOpen(state.tabs, state.activeTabId, tool, source);
				const activeTool = options.activeTool ?? null;
				const hint = options.hint ?? null;
				const activate = options.activate !== false;
				if (plan.action === 'focus') {
					openedId = plan.tabId;
					const tabs = state.tabs.map((tab) =>
						tab.id === plan.tabId && isToolTab(tab) && ((activeTool && tab.activeTool !== activeTool) || hint)
							? { ...tab, activeTool: activeTool ?? tab.activeTool ?? null, hint }
							: tab
					);
					return { ...state, tabs, activeTabId: activate ? plan.tabId : state.activeTabId };
				}
				if (plan.action === 'fill') {
					openedId = plan.tabId;
					const tabs = state.tabs.map((tab) =>
						tab.id === plan.tabId && isToolTab(tab)
							? {
									...tab,
									source,
									state: null,
									dirty: 0,
									run: null,
									thumbnail: null,
									activeTool: activeTool ?? tab.activeTool ?? null,
									hint,
									name: tab.renamed ? tab.name : (mediaFileStem(source) ?? tab.name)
								}
							: tab
					);
					return { ...state, tabs, activeTabId: activate ? plan.tabId : state.activeTabId };
				}
				const appended = appendToolTab(state, { tool, source, activeTool, hint }, activate);
				openedId = appended.id;
				return appended.state;
			});
			return openedId;
		},

		openToolTabs: (tool: string, sources: MediaRef[], options: OpenToolTabOptions = {}): string[] => {
			const ids: string[] = [];
			for (const source of sources) {
				ids.push(tabsStoreApi.openToolTab(tool, source, { ...options, activate: false }));
			}
			if (ids.length > 0) update((state) => ({ ...state, activeTabId: ids[0] }));
			return ids;
		},

		findToolTabFor: (source: MediaRef | null, tool?: string): ToolTab | null => {
			let found: ToolTab | null = null;
			subscribe((state) => (found = findToolTabFor(state.tabs, source, tool)))();
			return found;
		},

		renameTab: (tabId: string, name: string) => {
			update((state) => ({
				...state,
				tabs: state.tabs.map((tab) =>
					tab.id !== tabId ? tab : isToolTab(tab) ? { ...tab, name, renamed: true } : { ...tab, name }
				)
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
				activeTabId: id,
				lastGenerationTabId: id
			});
		}
	};
	return tabsStoreApi;
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
export const currentTab = selectorStore(
	($store): Tab => $store.tabs.find((t) => t.id === $store.activeTabId) || $store.tabs[0]
);

export const activeToolTab = selectorStore(($store): ToolTab | null => {
	const tab = $store.tabs.find((t) => t.id === $store.activeTabId);
	return isToolTab(tab) ? tab : null;
});

export function generationViewTab(state: TabsState): GenerationTab {
	const last = state.tabs.find((t) => t.id === state.lastGenerationTabId);
	return isGenerationTab(last) ? last : state.tabs.find(isGenerationTab)!;
}

export const activeTab = selectorStore(generationViewTab);

// The generation queue lets many tabs have in-flight work simultaneously, so
// there is no longer a single exclusive "generating tab" lock (the old
// `generatingTabId` field). These are derived straight from each tab's own
// `generation.isGenerating` flag instead of a separately-tracked id, so they
// can never point at a tab that finished (or drop a tab that started) due to
// a handler forgetting to clear/set a shared lock.
export const generatingTab = selectorStore(
	($store): GenerationTab | null => $store.tabs.filter(isGenerationTab).find((t) => t.generation.isGenerating) || null
);

export const isActiveTabGenerating = derived(activeTab, ($activeTab) => {
	return !!$activeTab?.generation.isGenerating;
});
