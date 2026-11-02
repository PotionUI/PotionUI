import { api } from '$lib/services/api';
import type { APIResponse } from '$lib/types/api';
import type {
	TabDraft,
	TabDraftOp,
	TabDraftOpsRequest,
	TabDraftDoc,
	TabDraftSummary
} from '$lib/services/api/tabDrafts';
import type { VariablesMap } from '$lib/utils/variableDefs';
import { toasts } from './toast';
import { tabsStore } from './tabs';
import { generationTabs } from '$lib/types/tabs';
import { tabDraftOutbox, type TabDraftOutbox } from './tabDraftOutbox';
import { diffVariables, overlayOps, sameForSync } from './tabDraftOps';

export const TAB_DRAFT_DEBOUNCE_MS = 500;
export const TAB_DRAFT_MAX_BACKOFF_MS = 30000;
export const TAB_DRAFT_MAX_OPS_PER_REQUEST = 500;

const EMPTY: VariablesMap = {};

interface TabLike {
	id: string;
	variables?: VariablesMap;
}

export interface TabsPort {
	subscribe(run: (state: { tabs: TabLike[] }) => void): () => void;
	setVariables(tabId: string, variables: VariablesMap): void;
}

export interface TabDraftApiPort {
	list(): Promise<APIResponse<TabDraftSummary[]>>;
	get(tabId: string): Promise<APIResponse<TabDraft | null>>;
	replace(tabId: string, doc: TabDraftDoc): Promise<APIResponse<TabDraft>>;
	apply(tabId: string, request: TabDraftOpsRequest): Promise<APIResponse<TabDraft>>;
	remove(tabId: string): Promise<void>;
}

export interface TabDraftNotifier {
	error(message: string): void;
	warning(message: string): void;
}

export interface TabDraftSyncDeps {
	tabs: TabsPort;
	api: TabDraftApiPort;
	outbox: TabDraftOutbox;
	notify: TabDraftNotifier;
	debounceMs?: number;
	maxBackoffMs?: number;
	isOnline?: () => boolean;
}

interface OperationFailure {
	index?: number | null;
	name?: string | null;
	errors?: string[];
}

interface Failure {
	status: number | null;
	message: string;
	operations: OperationFailure[];
}

type StepResult = 'ok' | 'retry' | 'stale';

const RETRYABLE_CLIENT_STATUSES = new Set([401, 403, 404, 408, 425, 429]);

function describeFailure(error: unknown): Failure {
	const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
	if (!response) return { status: null, message: '', operations: [] };
	const data = response.data as { detail?: unknown } | undefined;
	const detail = (data && typeof data === 'object' && 'detail' in data ? data.detail : data) as
		| { message?: string; operations?: OperationFailure[] }
		| undefined;
	return {
		status: typeof response.status === 'number' ? response.status : null,
		message: typeof detail?.message === 'string' ? detail.message : '',
		operations: Array.isArray(detail?.operations) ? detail.operations : []
	};
}

function isTransient(status: number | null): boolean {
	if (status === null) return true;
	if (status >= 500) return true;
	return RETRYABLE_CLIENT_STATUSES.has(status);
}

function sameMap(a: VariablesMap, b: VariablesMap): boolean {
	const keys = Object.keys(a);
	return keys.length === Object.keys(b).length && keys.every((key) => key in b && a[key] === b[key]);
}

export function createTabDraftSync(deps: TabDraftSyncDeps) {
	const { tabs, api: drafts, outbox, notify } = deps;
	const debounceMs = deps.debounceMs ?? TAB_DRAFT_DEBOUNCE_MS;
	const maxBackoffMs = deps.maxBackoffMs ?? TAB_DRAFT_MAX_BACKOFF_MS;
	const isOnline =
		deps.isOnline ?? (() => typeof navigator === 'undefined' || navigator.onLine !== false);

	const shadow = new Map<string, VariablesMap>();
	const revs = new Map<string, number>();
	const rejected = new Map<string, Map<string, TabDraftOp>>();

	let latest: TabLike[] = [];
	let seeded = false;
	let started = false;
	let booted = false;
	let bootRunning = false;
	let flushing = false;
	let flushAgain = false;
	let limitWarned = false;
	let storageWarned = false;
	let session = 0;
	let retryAttempt = 0;
	let flushTimer: ReturnType<typeof setTimeout> | null = null;
	let retryTimer: ReturnType<typeof setTimeout> | null = null;
	let unsubscribe: (() => void) | null = null;
	const waiters: Array<(ready: boolean) => void> = [];

	function applyLocal(tabId: string, variables: VariablesMap): void {
		shadow.set(tabId, variables);
		tabs.setVariables(tabId, variables);
	}

	function pendingFor(tabId: string): TabDraftOp[] {
		return [...(rejected.get(tabId)?.values() ?? []), ...outbox.pendingOps(tabId)];
	}

	function resolveMerge(tabId: string, serverVariables: VariablesMap): VariablesMap | null {
		const current = shadow.get(tabId) ?? EMPTY;
		const result = overlayOps(serverVariables, pendingFor(tabId));
		for (const name of Object.keys(result)) {
			if (name in current && sameForSync(current[name], result[name])) result[name] = current[name];
		}
		return sameMap(result, current) ? null : result;
	}

	function mergeServer(tabId: string, serverVariables: VariablesMap): void {
		if (!shadow.has(tabId)) return;
		const merged = resolveMerge(tabId, outbox.pendingReplace(tabId) ?? serverVariables);
		if (merged) applyLocal(tabId, merged);
	}

	function onState(state: { tabs: TabLike[] }): void {
		latest = state.tabs;
		let changed = false;
		const present = new Set<string>();
		const overlays: Array<[string, VariablesMap]> = [];

		for (const tab of state.tabs) {
			present.add(tab.id);
			const next = tab.variables ?? EMPTY;
			const prev = shadow.get(tab.id);
			if (prev === next) continue;
			shadow.set(tab.id, next);
			if (!seeded) {
				const pending = outbox.pendingOps(tab.id);
				const replacement = outbox.pendingReplace(tab.id);
				if (pending.length > 0 || replacement) overlays.push([tab.id, overlayOps(replacement ?? next, pending)]);
				continue;
			}
			const ops = diffVariables(prev ?? EMPTY, next);
			if (ops.length === 0) continue;
			if (!outbox.putOps(tab.id, ops)) warnStorage();
			const names = ops.map((op) => op.name);
			const bucket = rejected.get(tab.id);
			for (const name of names) bucket?.delete(name);
			changed = true;
		}

		if (seeded) {
			for (const id of [...shadow.keys()]) {
				if (present.has(id)) continue;
				shadow.delete(id);
				revs.delete(id);
				rejected.delete(id);
				outbox.queueDelete(id);
				changed = true;
			}
		}
		seeded = true;

		for (const [id, variables] of overlays) applyLocal(id, variables);
		if (changed) scheduleFlush();
	}

	function scheduleFlush(): void {
		if (!started || !booted) return;
		if (flushTimer) clearTimeout(flushTimer);
		flushTimer = setTimeout(() => {
			flushTimer = null;
			void flush();
		}, debounceMs);
	}

	function scheduleRetry(): void {
		if (!started) return;
		if (retryTimer) clearTimeout(retryTimer);
		const delay = Math.min(1000 * 2 ** retryAttempt, maxBackoffMs);
		retryAttempt += 1;
		retryTimer = setTimeout(() => {
			retryTimer = null;
			if (booted) void flush();
			else void boot();
		}, delay);
	}

	function warnLimit(): void {
		if (limitWarned) return;
		limitWarned = true;
		notify.warning(
			'Too many tabs have synced variables. Close old tabs so new variable edits can sync; edits are kept and will retry.'
		);
	}

	function warnStorage(): void {
		if (storageWarned) return;
		storageWarned = true;
		notify.warning(
			'Could not save variable edits in this browser. They sync only while this page stays open.'
		);
	}

	function reject(tabId: string, ops: TabDraftOp[]): void {
		outbox.ackOps(tabId, ops);
		if (!shadow.has(tabId)) return;
		const bucket = rejected.get(tabId) ?? new Map<string, TabDraftOp>();
		for (const op of ops) bucket.set(op.name, op);
		rejected.set(tabId, bucket);
	}

	function describeOps(ops: TabDraftOp[]): string {
		return ops.length === 1
			? `Variable "${ops[0].name}"`
			: `${ops.length} variable edits (${ops.map((op) => `"${op.name}"`).join(', ')})`;
	}

	async function flushReplace(tabId: string): Promise<StepResult> {
		const mine = session;
		const doc = outbox.pendingReplace(tabId);
		if (!doc) return 'ok';
		try {
			const response = await drafts.replace(tabId, { variables: doc });
			if (mine !== session) return 'stale';
			outbox.ackReplace(tabId, doc);
			limitWarned = false;
			if (response.data) revs.set(tabId, response.data.rev);
			return 'ok';
		} catch (error) {
			if (mine !== session) return 'stale';
			const failure = describeFailure(error);
			if (failure.status === 409) {
				warnLimit();
				return 'retry';
			}
			if (isTransient(failure.status)) return 'retry';
			outbox.ackReplace(tabId, doc);
			const queued = new Set(outbox.pendingOps(tabId).map((op) => op.name));
			const fresh = diffVariables(EMPTY, doc).filter((op) => !queued.has(op.name));
			if (!outbox.putOps(tabId, fresh)) warnStorage();
			return 'ok';
		}
	}

	async function flushTab(tabId: string): Promise<StepResult> {
		const mine = session;
		const replaced = await flushReplace(tabId);
		if (replaced !== 'ok') return replaced;
		for (;;) {
			const chunk = outbox.pendingOps(tabId).slice(0, TAB_DRAFT_MAX_OPS_PER_REQUEST);
			if (chunk.length === 0) return 'ok';
			try {
				const base = revs.get(tabId);
				const response = await drafts.apply(tabId, {
					ops: chunk,
					...(base === undefined ? {} : { base_rev: base })
				});
				if (mine !== session) return 'stale';
				outbox.ackOps(tabId, chunk);
				limitWarned = false;
				const draft = response.data;
				if (draft) {
					revs.set(tabId, draft.rev);
					mergeServer(tabId, draft.doc?.variables ?? {});
				}
			} catch (error) {
				if (mine !== session) return 'stale';
				const failure = describeFailure(error);
				if (failure.status === 409) {
					warnLimit();
					return 'retry';
				}
				if (isTransient(failure.status)) return 'retry';
				if (failure.status === 413) {
					reject(tabId, chunk);
					notify.error(
						`${describeOps(chunk)} too large to sync. It stays in this tab but is not saved to the server.`
					);
					continue;
				}
				const flagged = failure.operations
					.map((entry) => (typeof entry.index === 'number' ? chunk[entry.index] : undefined))
					.filter((op): op is TabDraftOp => op !== undefined);
				const bad = failure.status === 422 && flagged.length > 0 ? flagged : chunk;
				reject(tabId, bad);
				const first = failure.operations.find((entry) => entry.errors && entry.errors.length > 0);
				const reason = first?.errors?.[0] ?? failure.message;
				notify.error(
					`${describeOps(bad)} not synced${reason ? `: ${reason}` : '.'} The edit stays in this tab but is not saved.`
				);
			}
		}
	}

	async function flush(): Promise<void> {
		if (!started) return;
		if (flushing) {
			flushAgain = true;
			return;
		}
		if (!booted || !isOnline()) return;
		const mine = session;
		flushing = true;
		let retry = false;
		try {
			for (const id of outbox.pendingDeletes()) {
				try {
					await drafts.remove(id);
					if (mine !== session) return;
					outbox.ackDelete(id);
				} catch (error) {
					if (mine !== session) return;
					const failure = describeFailure(error);
					if (failure.status === 404 || !isTransient(failure.status)) outbox.ackDelete(id);
					else {
						retry = true;
						break;
					}
				}
			}
			if (!retry) {
				for (const id of outbox.pendingTabIds()) {
					const result = await flushTab(id);
					if (result === 'stale') return;
					if (result === 'retry') {
						retry = true;
						break;
					}
				}
			}
		} finally {
			if (mine === session) flushing = false;
		}
		if (retry) {
			scheduleRetry();
			return;
		}
		retryAttempt = 0;
		if (flushAgain) {
			flushAgain = false;
			void flush();
		}
	}

	async function fetchDraft(tabId: string): Promise<TabDraft | null | 'retry' | 'stale'> {
		const mine = session;
		try {
			const response = await drafts.get(tabId);
			if (mine !== session) return 'stale';
			return response.data ?? null;
		} catch (error) {
			if (mine !== session) return 'stale';
			const failure = describeFailure(error);
			if (failure.status === 404) return null;
			return 'retry';
		}
	}

	async function migrateTab(tabId: string, variables: VariablesMap): Promise<StepResult> {
		const mine = session;
		try {
			const response = await drafts.replace(tabId, { variables });
			if (mine !== session) return 'stale';
			if (response.data) revs.set(tabId, response.data.rev);
			outbox.clearLegacy(tabId);
			return 'ok';
		} catch (error) {
			if (mine !== session) return 'stale';
			const failure = describeFailure(error);
			if (isTransient(failure.status)) return 'retry';
			if (failure.status === 409) warnLimit();
			outbox.clearLegacy(tabId);
			const queued = new Set(outbox.pendingOps(tabId).map((op) => op.name));
			const fresh = diffVariables(EMPTY, variables).filter((op) => !queued.has(op.name));
			if (!outbox.putOps(tabId, fresh)) warnStorage();
			return 'ok';
		}
	}

	async function boot(): Promise<void> {
		if (!started || booted || bootRunning) return;
		if (!isOnline()) return;
		const mine = session;
		bootRunning = true;
		try {
			let summaries: TabDraftSummary[];
			try {
				summaries = (await drafts.list()).data ?? [];
			} catch {
				if (mine === session) scheduleRetry();
				return;
			}
			if (mine !== session) return;
			const known = new Set(summaries.map((summary) => summary.tab_id));
			const legacy = outbox.legacyEntries();
			const openIds = latest.map((tab) => tab.id);
			for (const id of Object.keys(legacy)) {
				if (!openIds.includes(id)) outbox.clearLegacy(id);
			}
			let retry = false;
			for (const id of openIds) {
				if (known.has(id)) {
					outbox.clearLegacy(id);
					const draft = await fetchDraft(id);
					if (draft === 'stale') return;
					if (draft === 'retry') {
						retry = true;
						continue;
					}
					if (draft) {
						revs.set(id, draft.rev);
						mergeServer(id, draft.doc?.variables ?? {});
					}
				} else if (legacy[id]) {
					const result = await migrateTab(id, legacy[id]);
					if (result === 'stale') return;
					if (result === 'retry') retry = true;
				}
			}
			if (retry) {
				scheduleRetry();
				return;
			}
			outbox.markMigrated();
			booted = true;
			retryAttempt = 0;
			for (const wake of [...waiters]) wake(true);
			void flush();
		} finally {
			if (mine === session) bootRunning = false;
		}
	}

	function onOnline(): void {
		retryAttempt = 0;
		if (retryTimer) clearTimeout(retryTimer);
		retryTimer = null;
		if (booted) void flush();
		else void boot();
	}

	function start(): void {
		if (started) return;
		started = true;
		session += 1;
		seeded = false;
		if (typeof window !== 'undefined') window.addEventListener('online', onOnline);
		unsubscribe = tabs.subscribe(onState);
		void boot();
		if (outbox.pendingTabIds().length > 0 || outbox.pendingDeletes().length > 0) scheduleFlush();
	}

	function stop(): void {
		if (!started) return;
		started = false;
		for (const wake of [...waiters]) wake(false);
		session += 1;
		flushing = false;
		flushAgain = false;
		bootRunning = false;
		booted = false;
		seeded = false;
		unsubscribe?.();
		unsubscribe = null;
		if (typeof window !== 'undefined') window.removeEventListener('online', onOnline);
		if (flushTimer) clearTimeout(flushTimer);
		if (retryTimer) clearTimeout(retryTimer);
		flushTimer = null;
		retryTimer = null;
		shadow.clear();
		revs.clear();
		rejected.clear();
			limitWarned = false;
		storageWarned = false;
		retryAttempt = 0;
		outbox.reset();
	}

	function receiveDraft(tabId: string, doc: TabDraftDoc | null | undefined, rev: number): void {
		if (!started || !shadow.has(tabId)) return;
		const known = revs.get(tabId);
		if (typeof rev === 'number' && known !== undefined && rev <= known) return;
		if (typeof rev === 'number') revs.set(tabId, rev);
		mergeServer(tabId, doc?.variables ?? {});
	}

	function replaceDraft(tabId: string, variables: VariablesMap): void {
		if (!started || !shadow.has(tabId)) return;
		rejected.delete(tabId);
		shadow.set(tabId, variables);
		if (booted && Object.keys(variables).length === 0 && !revs.has(tabId) && !outbox.pendingReplace(tabId)) {
			outbox.dropOps(tabId);
			return;
		}
		if (!outbox.putReplace(tabId, variables)) warnStorage();
		scheduleFlush();
	}

	function settled(timeoutMs?: number): Promise<boolean> {
		if (booted) return Promise.resolve(true);
		if (!started) return Promise.resolve(false);
		return new Promise((resolve) => {
			const timer = timeoutMs === undefined ? null : setTimeout(() => finish(false), timeoutMs);
			function finish(ready: boolean) {
				if (timer) clearTimeout(timer);
				const index = waiters.indexOf(finish);
				if (index >= 0) waiters.splice(index, 1);
				resolve(ready);
			}
			waiters.push(finish);
		});
	}

	async function flushNow(): Promise<void> {
		if (flushTimer) clearTimeout(flushTimer);
		flushTimer = null;
		await flush();
	}

	return { start, stop, flushNow, receiveDraft, replaceDraft, settled, revOf: (tabId: string) => revs.get(tabId) };
}

export const tabDraftSync = createTabDraftSync({
	tabs: {
		subscribe: (run) => tabsStore.subscribe((state) => run({ tabs: generationTabs(state.tabs) })),
		setVariables: (tabId, variables) => tabsStore.updateTab(tabId, { variables })
	},
	api: {
		list: () => api.listTabDrafts(),
		get: (tabId) => api.getTabDraft(tabId),
		replace: (tabId, doc) => api.replaceTabDraft(tabId, doc),
		apply: (tabId, request) => api.applyTabDraftOps(tabId, request),
		remove: (tabId) => api.deleteTabDraft(tabId)
	},
	outbox: tabDraftOutbox,
	notify: {
		error: (message) => toasts.error(message, 10000),
		warning: (message) => toasts.warning(message, 10000)
	}
});

export const sessionDrafts = {
	replaceVariables: (tabId: string, variables: VariablesMap) => tabDraftSync.replaceDraft(tabId, variables)
};
