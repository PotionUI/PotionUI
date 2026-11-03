import { writable, type Readable } from 'svelte/store';
import { api } from '$lib/services/api';
import { toasts } from '$lib/stores/toast';
import { tabsStore } from '$lib/stores/tabs';
import { generationTabs } from '$lib/types/tabs';
import type { APIResponse, ModeBasedSessionData, Session, SessionData, SessionWriteOrigin } from '$lib/types/api';
import type { GenerationTab, ModeState } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import type { SegmentJoin } from '$lib/utils/richSegments';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { collectTabSessionData } from '$lib/utils/sessionTabState';
import { modeStateFromSessionData } from '$lib/utils/modeState';
import { isSameWindow, sessionClientId, WINDOW_CLIENT_ID } from '$lib/session/clientIdentity';
import {
	fieldValue,
	mergeSessionData,
	sameValue,
	withFieldValue,
	type SessionMergeOptions,
	type SyncConflict
} from '$lib/session/sessionMerge';

export const LIVE_HIGHLIGHT_MS = 2200;
export const SAME_WINDOW_LABEL = 'another tab';

const LIVE_FIELDS = [
	'prompt',
	'negativePrompt',
	'promptSegments',
	'negativePromptSegments',
	'promptTabs',
	'activePromptTab',
	'promptRelay',
	'videoDirector',
	'musicDirector',
	'formData',
	'seed',
	'selectedVariant'
] as const;

export interface PendingConflict extends SyncConflict {
	id: string;
	label: string;
}

export interface TabSyncMarks {
	sessionId: string | null;
	lastLabel?: string;
	highlights: Record<string, number>;
	conflicts: PendingConflict[];
	unseen: boolean;
}

export interface SessionChangedMessage {
	session_id: string;
	version: number;
	origin?: SessionWriteOrigin | null;
	client_id?: string | null;
}

export interface LiveSyncTabsState {
	tabs: GenerationTab[];
	activeTabId: string;
}

export interface LiveSyncDeps {
	tabs: {
		subscribe(run: (state: LiveSyncTabsState) => void): () => void;
		updateTab(tabId: string, patch: Partial<GenerationTab>): void;
	};
	api: { getSessionById(sessionId: string): Promise<APIResponse<Session>> };
	notify: { info(message: string): void };
	windowId?: string;
	highlightMs?: number;
	timers?: {
		setTimeout(handler: () => void, ms: number): unknown;
		clearTimeout(id: unknown): void;
	};
}

const EMPTY_MARKS: TabSyncMarks = { sessionId: null, highlights: {}, conflicts: [], unseen: false };
const MAX_FETCHES = 3;

function capitalize(text: string): string {
	return text.charAt(0).toUpperCase() + text.slice(1);
}

function conflictId(mode: string, key: string): string {
	return `${mode}|${key}`;
}

function parseBaseline(signature: string | null | undefined): ModeBasedSessionData | null {
	if (!signature) return null;
	try {
		return JSON.parse(signature) as ModeBasedSessionData;
	} catch {
		return null;
	}
}

function liveFieldsPatch(tab: GenerationTab, entry: SessionData): Partial<GenerationTab> {
	const patch: Record<string, unknown> = {};
	for (const field of LIVE_FIELDS) {
		const next = entry[field];
		const current = field === 'selectedVariant' ? tab.selectedVariant || undefined : tab[field];
		if (!sameValue(next, current)) patch[field] = field === 'formData' ? (next ?? {}) : next;
	}
	const variables = entry.variables ?? {};
	if (!sameValue(variables, tab.variables ?? {})) patch.variables = variables;
	return patch as Partial<GenerationTab>;
}

function cacheFrom(data: ModeBasedSessionData, activeMode: string, current: Record<string, ModeState> | undefined) {
	let changed = false;
	const next: Record<string, ModeState> = { ...(current ?? {}) };
	for (const [mode, entry] of Object.entries(data)) {
		if (mode === activeMode) continue;
		const state = modeStateFromSessionData(entry);
		if (!sameValue(state, next[mode])) {
			next[mode] = state;
			changed = true;
		}
	}
	return { next, changed };
}

export function remoteChangeLabel(marks: TabSyncMarks | undefined): string | null {
	if (!marks?.unseen) return null;
	return marks.lastLabel ? `${capitalize(marks.lastLabel)} changed this session` : 'Changed elsewhere';
}

export function createSessionLiveSync(deps: LiveSyncDeps) {
	const windowId = deps.windowId ?? WINDOW_CLIENT_ID;
	const highlightMs = deps.highlightMs ?? LIVE_HIGHLIGHT_MS;
	const timers = deps.timers ?? {
		setTimeout: (handler: () => void, ms: number) => setTimeout(handler, ms),
		clearTimeout: (id: unknown) => clearTimeout(id as ReturnType<typeof setTimeout>)
	};

	const marksStore = writable<Record<string, TabSyncMarks>>({});
	const recordsStore = writable<Record<string, Session>>({});
	let marks: Record<string, TabSyncMarks> = {};
	let records: Record<string, Session> = {};
	let tabs: GenerationTab[] = [];
	let activeTabId = '';
	let unsubscribe: (() => void) | null = null;
	let segmentJoin: (presetId: string | null) => SegmentJoin = () => 'space';
	let highlightSeq = 0;
	const keptMine = new Set<string>();
	const chains = new Map<string, Promise<void>>();
	const highlightTimers = new Map<string, unknown>();
	const saveListeners = new Set<(tabId: string) => void>();

	function setMarks(tabId: string, next: TabSyncMarks | null) {
		const copy = { ...marks };
		if (next && (next.unseen || next.conflicts.length > 0 || Object.keys(next.highlights).length > 0)) copy[tabId] = next;
		else delete copy[tabId];
		marks = copy;
		marksStore.set(marks);
	}

	function marksOf(tabId: string): TabSyncMarks {
		return marks[tabId] ?? EMPTY_MARKS;
	}

	function findTab(tabId: string): GenerationTab | undefined {
		return tabs.find((tab) => tab.id === tabId);
	}

	function mergeOptions(tab: GenerationTab): SessionMergeOptions {
		const join = segmentJoin(tab.selectedPreset);
		return { resolvePrompt: (segments: Segment[]) => resolvePromptSegments(segments, join) };
	}

	function expireHighlights(tabId: string) {
		const timerKey = tabId;
		const existing = highlightTimers.get(timerKey);
		if (existing !== undefined) timers.clearTimeout(existing);
		const stamp = highlightSeq;
		highlightTimers.set(
			timerKey,
			timers.setTimeout(() => {
				highlightTimers.delete(timerKey);
				const current = marksOf(tabId);
				const highlights = Object.fromEntries(
					Object.entries(current.highlights).filter(([, value]) => value > stamp)
				);
				setMarks(tabId, { ...current, highlights });
			}, highlightMs)
		);
	}

	function highlight(tabId: string, keys: string[]) {
		if (keys.length === 0) return;
		highlightSeq += 1;
		const current = marksOf(tabId);
		const highlights = { ...current.highlights };
		for (const key of keys) highlights[key] = highlightSeq;
		setMarks(tabId, { ...current, highlights });
		if (tabId === activeTabId) expireHighlights(tabId);
	}

	function labelFor(origin: SessionWriteOrigin | null | undefined, clientId: string | null | undefined): string {
		if (isSameWindow(clientId, windowId)) return SAME_WINDOW_LABEL;
		return origin?.label || 'Someone';
	}

	function localData(tab: GenerationTab, sessionId: string, base: ModeBasedSessionData | null, mode: string) {
		const saved = base ?? records[sessionId]?.data ?? {};
		return collectTabSessionData(tab, mode, saved, base?.[mode]?.presetVersion);
	}

	function writeEntry(tab: GenerationTab, mode: string, entry: SessionData): Partial<GenerationTab> {
		if (mode === tab.selectedMode) return liveFieldsPatch(tab, entry);
		const cache = { ...(tab.modeStateByMode ?? {}), [mode]: modeStateFromSessionData(entry) };
		return { modeStateByMode: cache };
	}

	function carryConflicts(
		previous: PendingConflict[],
		fresh: PendingConflict[],
		local: ModeBasedSessionData,
		remote: ModeBasedSessionData
	): PendingConflict[] {
		const byId = new Map(fresh.map((conflict) => [conflict.id, conflict]));
		const carried: PendingConflict[] = [];
		for (const conflict of previous) {
			if (byId.has(conflict.id)) continue;
			const remoteValue = fieldValue(remote[conflict.mode], conflict.key);
			if (sameValue(fieldValue(local[conflict.mode], conflict.key), remoteValue)) continue;
			carried.push({ ...conflict, remote: remoteValue });
		}
		return [...carried, ...fresh];
	}

	function applyToTab(tab: GenerationTab, session: Session, label: string, quiet: boolean) {
		const mode = tab.selectedMode;
		if (!mode || session.preset_id !== tab.selectedPreset) return;
		if (tab.savedSessionSignature === undefined) return;
		if (typeof tab.sessionVersion === 'number' && tab.sessionVersion >= session.version) return;

		const base = parseBaseline(tab.savedSessionSignature);
		const options = mergeOptions(tab);
		const local = localData(tab, session.id, base, mode);
		const result = mergeSessionData(base, local, session.data, options);
		const presetVersion = base?.[mode]?.presetVersion;

		const merged = result.merged[mode] ?? {};
		const theirs = result.theirs[mode] ?? {};
		const mergedCache = cacheFrom(result.merged, mode, tab.modeStateByMode);
		const theirsCache = cacheFrom(result.theirs, mode, tab.modeStateByMode);
		const theirsTab: GenerationTab = {
			...tab,
			...liveFieldsPatch(tab, theirs),
			modeStateByMode: theirsCache.next
		};
		const patch: Partial<GenerationTab> = {
			...liveFieldsPatch(tab, merged),
			...(mergedCache.changed ? { modeStateByMode: mergedCache.next } : {}),
			sessionVersion: session.version,
			savedSessionSignature: JSON.stringify(
				collectTabSessionData(theirsTab, mode, session.data, presetVersion)
			)
		};

		const fresh = result.conflicts.map((conflict) => ({
			...conflict,
			id: conflictId(conflict.mode, conflict.key),
			label
		}));
		const current = marksOf(tab.id);
		const conflicts = carryConflicts(current.conflicts, fresh, result.merged, session.data);
		const visible = result.applied.filter((change) => change.mode === mode).map((change) => change.key);
		const touched = result.applied.length > 0 || fresh.length > 0;
		const background = tab.id !== activeTabId;

		deps.tabs.updateTab(tab.id, patch);
		setMarks(tab.id, {
			...current,
			sessionId: session.id,
			lastLabel: touched && !quiet ? label : current.lastLabel,
			conflicts,
			unseen: current.unseen || (background && touched && !quiet)
		});
		if (!quiet) highlight(tab.id, visible);
		if (background && touched && !quiet) deps.notify.info(`${capitalize(label)} updated ${tab.name}`);
	}

	function boundTabs(sessionId: string, skipClientId: string | null | undefined): GenerationTab[] {
		return tabs.filter(
			(tab) => tab.selectedSessionId === sessionId && sessionClientId(tab.id, windowId) !== skipClientId
		);
	}

	function publishRecord(session: Session) {
		const known = records[session.id];
		if (known && known.version >= session.version) return;
		records = { ...records, [session.id]: session };
		recordsStore.set(records);
	}

	function enqueue(sessionId: string, job: () => Promise<void>): Promise<void> {
		const previous = chains.get(sessionId) ?? Promise.resolve();
		const next = previous.then(job, job);
		chains.set(sessionId, next);
		void next.finally(() => {
			if (chains.get(sessionId) === next) chains.delete(sessionId);
		});
		return next;
	}

	async function fetchAtLeast(sessionId: string, version: number): Promise<Session | null> {
		for (let attempt = 0; attempt < MAX_FETCHES; attempt += 1) {
			let response: APIResponse<Session>;
			try {
				response = await deps.api.getSessionById(sessionId);
			} catch {
				return null;
			}
			const session = response.success ? response.data : undefined;
			if (!session) return null;
			if (session.version >= version) return session;
		}
		return null;
	}

	function mergeInto(session: Session, label: string, skipClientId: string | null | undefined, quietTabId?: string) {
		publishRecord(session);
		for (const tab of boundTabs(session.id, skipClientId)) {
			applyToTab(tab, session, label, tab.id === quietTabId);
		}
	}

	function receive(message: SessionChangedMessage): Promise<void> {
		if (!unsubscribe || !message?.session_id || typeof message.version !== 'number') return Promise.resolve();
		const sessionId = message.session_id;
		return enqueue(sessionId, async () => {
			const waiting = boundTabs(sessionId, message.client_id).filter(
				(tab) => !(typeof tab.sessionVersion === 'number' && tab.sessionVersion >= message.version)
			);
			if (waiting.length === 0) return;
			const session = await fetchAtLeast(sessionId, message.version);
			if (!session || !unsubscribe) return;
			mergeInto(session, labelFor(message.origin, message.client_id), message.client_id);
		});
	}

	function reconcile(
		tabId: string,
		session: Session,
		origin: SessionWriteOrigin | null | undefined,
		clientId: string | null | undefined
	): Promise<void> {
		return enqueue(session.id, async () => {
			const quiet = clientId === sessionClientId(tabId, windowId) ? tabId : undefined;
			mergeInto(session, labelFor(origin, clientId), undefined, quiet);
		});
	}

	function resolve(tabId: string, id: string, choice: 'theirs' | 'mine') {
		const current = marksOf(tabId);
		const conflict = current.conflicts.find((item) => item.id === id);
		const tab = findTab(tabId);
		if (!conflict || !tab) return;
		const writes = choice === 'theirs' || conflict.local === undefined;
		const value = choice === 'theirs' ? conflict.remote : conflict.local;
		const sessionId = tab.selectedSessionId ?? '';
		const base = parseBaseline(tab.savedSessionSignature);
		if (writes && tab.selectedMode) {
			const local = localData(tab, sessionId, base, conflict.mode);
			const options = mergeOptions(tab);
			const entry = withFieldValue(local[conflict.mode] ?? {}, conflict.key, value, options);
			deps.tabs.updateTab(tabId, writeEntry(tab, conflict.mode, entry));
		}
		const conflicts = current.conflicts.filter((item) => item.id !== id);
		setMarks(tabId, { ...current, conflicts });
		if (choice === 'theirs' && conflict.mode === tab.selectedMode) highlight(tabId, [conflict.key]);
		if (choice === 'mine') keptMine.add(tabId);
		if (conflicts.length === 0 && keptMine.delete(tabId)) {
			for (const listener of [...saveListeners]) listener(tabId);
		}
	}

	function onState(state: LiveSyncTabsState) {
		const previousActive = activeTabId;
		tabs = state.tabs;
		activeTabId = state.activeTabId;
		const present = new Set(tabs.map((tab) => tab.id));
		for (const tabId of Object.keys(marks)) {
			if (!present.has(tabId)) {
				keptMine.delete(tabId);
				setMarks(tabId, null);
				continue;
			}
			const tab = findTab(tabId);
			const current = marks[tabId];
			if (current.conflicts.length > 0 && tab && tab.selectedSessionId !== current.sessionId) {
				keptMine.delete(tabId);
				setMarks(tabId, { ...current, conflicts: [] });
			}
		}
		if (activeTabId !== previousActive && marks[activeTabId]) {
			const current = marks[activeTabId];
			if (current.unseen) setMarks(activeTabId, { ...current, unseen: false });
			if (Object.keys(current.highlights).length > 0) expireHighlights(activeTabId);
		}
	}

	function start() {
		if (unsubscribe) return;
		unsubscribe = deps.tabs.subscribe(onState);
	}

	function stop() {
		unsubscribe?.();
		unsubscribe = null;
		for (const timer of highlightTimers.values()) timers.clearTimeout(timer);
		highlightTimers.clear();
		chains.clear();
		keptMine.clear();
		marks = {};
		records = {};
		marksStore.set(marks);
		recordsStore.set(records);
	}

	return {
		marks: { subscribe: marksStore.subscribe } as Readable<Record<string, TabSyncMarks>>,
		records: { subscribe: recordsStore.subscribe } as Readable<Record<string, Session>>,
		start,
		stop,
		receive,
		reconcile,
		resolve,
		hasConflicts: (tabId: string) => marksOf(tabId).conflicts.length > 0,
		clientIdFor: (tabId: string) => sessionClientId(tabId, windowId),
		onSaveRequest(listener: (tabId: string) => void) {
			saveListeners.add(listener);
			return () => {
				saveListeners.delete(listener);
			};
		},
		configure(options: { segmentJoin?: (presetId: string | null) => SegmentJoin }) {
			if (options.segmentJoin) segmentJoin = options.segmentJoin;
		}
	};
}

export type SessionLiveSync = ReturnType<typeof createSessionLiveSync>;

export const sessionLiveSync = createSessionLiveSync({
	tabs: {
		subscribe: (run) =>
			tabsStore.subscribe((state) => run({ tabs: generationTabs(state.tabs), activeTabId: state.activeTabId })),
		updateTab: (tabId, patch) => tabsStore.updateTab(tabId, patch)
	},
	api: { getSessionById: (sessionId) => api.getSessionById(sessionId) },
	notify: { info: (message) => toasts.info(message, 6000) }
});
