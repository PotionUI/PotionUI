import { writable, type Readable } from 'svelte/store';
import type { EmbeddedBaseline, GenerationTab } from '$lib/types/tabs';
import { followedStatus, sessionMode } from './follow';
import {
	STEP_KEYS,
	canEditStep,
	editCount,
	nodeFingerprint,
	observeLoaded,
	stepOverrides,
	stepSettings,
	stepTabPatch,
	stepTitle,
	type StepLoaded,
	type StepOption
} from './stepEdit';
import type { ElixirNode, FollowedState, NodeOverrides } from './types';

export type CommitResult = 'saved' | 'conflict' | 'error';
export type After = 'leave' | { switchTo: string };

export interface StepEmbeddedTabs {
	open(data: Partial<GenerationTab> & { nodeId: string; persistence: 'local' | 'session' }): string;
	close(): void;
	current(): GenerationTab | null;
	setBaseline(baseline: EmbeddedBaseline): void;
	subscribe(run: (tab: GenerationTab | null) => void): () => void;
}

export interface LatestElixir {
	revision: number;
	node(id: string): ElixirNode | null;
	record: unknown;
}

export interface StepEditorDeps {
	elixirName(): string;
	options(): StepOption[];
	node(id: string): ElixirNode | null;
	followed(id: string): FollowedState | undefined;
	revision(): number;
	settle(): Promise<boolean>;
	fetchLatest(): Promise<LatestElixir | null>;
	adopt(record: unknown): void;
	commit(nodeId: string, overrides: NodeOverrides): Promise<CommitResult>;
	tabs: StepEmbeddedTabs;
	host: { setTitle(title: string): void; setDirty(edits: number): void };
	loadSession(tab: GenerationTab): Promise<void>;
	saveSession(tabId: string): Promise<boolean>;
	watchSessionDirty(tabId: string, run: (dirty: boolean) => void): () => void;
	notify: { error(message: string): void; saved(message: string, undo: () => void): void };
	onReturn(nodeId: string, saved: boolean): void;
	now(): number;
	timers?: { set(run: () => void, ms: number): unknown; clear(handle: unknown): void };
	autosaveMs?: number;
}

export interface StepConfirm {
	reason: 'leave' | 'switch';
	target: string | null;
}

export interface StepConflict {
	reason: 'changed' | 'gone';
	canOverwrite: boolean;
	after: After;
}

export interface StepEditorState {
	nodeId: string | null;
	label: string;
	follow: boolean;
	sessionName: string | null;
	edits: number;
	saving: boolean;
	opening: boolean;
	confirm: StepConfirm | null;
	conflict: StepConflict | null;
}

export const IDLE_STEP: StepEditorState = {
	nodeId: null,
	label: '',
	follow: false,
	sessionName: null,
	edits: 0,
	saving: false,
	opening: false,
	confirm: null,
	conflict: null
};

export interface StepEditor {
	state: Readable<StepEditorState>;
	snapshot(): StepEditorState;
	open(nodeId: string): Promise<boolean>;
	resume(): Promise<boolean>;
	go(nodeId: string): Promise<void>;
	back(): Promise<void>;
	save(after?: After, options?: { overwrite?: boolean }): Promise<CommitResult | 'noop'>;
	answerConfirm(choice: 'save' | 'discard' | 'cancel'): Promise<void>;
	resolveConflict(choice: 'cancel' | 'reload' | 'overwrite'): Promise<void>;
	destroy(): void;
}

const AUTOSAVE_MS = 1500;

export function createStepEditor(deps: StepEditorDeps): StepEditor {
	const store = writable<StepEditorState>(IDLE_STEP);
	let current: StepEditorState = IDLE_STEP;
	let loaded: StepLoaded | null = null;
	let openedRevision = 0;
	let openedPrint = '';
	let unwatch: (() => void) | null = null;
	let unwatchSession: (() => void) | null = null;
	let autosaveHandle: unknown = null;
	let lastSeen: unknown[] = [];
	let generation = 0;
	const timers = deps.timers ?? {
		set: (run: () => void, ms: number) => setTimeout(run, ms),
		clear: (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>)
	};

	function remember() {
		if (!loaded || !current.nodeId || current.follow) return;
		deps.tabs.setBaseline({ ...loaded, revision: openedRevision, print: openedPrint });
	}

	function put(next: Partial<StepEditorState>) {
		current = { ...current, ...next };
		store.set(current);
	}

	function stopWatching() {
		unwatch?.();
		unwatch = null;
		unwatchSession?.();
		unwatchSession = null;
		if (autosaveHandle !== null) timers.clear(autosaveHandle);
		autosaveHandle = null;
		lastSeen = [];
	}

	function observe(tab: GenerationTab | null) {
		if (!tab || !current.nodeId || current.follow) return;
		const refs: unknown[] = [tab.formData, tab.formPublished, ...STEP_KEYS.map((key) => (tab as unknown as Record<string, unknown>)[key])];
		if (refs.length === lastSeen.length && refs.every((ref, index) => Object.is(ref, lastSeen[index]))) return;
		lastSeen = refs;
		const before = loaded;
		loaded = observeLoaded(loaded, tab, deps.now());
		if (loaded !== before) remember();
		const edits = editCount(tab, loaded);
		if (edits !== current.edits) {
			put({ edits });
			deps.host.setDirty(edits);
		}
	}

	function scheduleAutosave(tabId: string) {
		if (autosaveHandle !== null) timers.clear(autosaveHandle);
		autosaveHandle = timers.set(() => {
			autosaveHandle = null;
			void deps.saveSession(tabId);
		}, deps.autosaveMs ?? AUTOSAVE_MS);
	}

	async function open(nodeId: string): Promise<boolean> {
		const node = deps.node(nodeId);
		if (!node || !canEditStep(node)) {
			deps.notify.error('Choose a preset for this step first.');
			return false;
		}
		const follow = sessionMode(node) === 'follow';
		const sessionId = node.session_ref?.id ?? null;
		if (follow && (!sessionId || followedStatus(node, deps.followed(nodeId)) !== 'ok')) {
			deps.notify.error('Its session is not available. Pick another session or keep a copy first.');
			return false;
		}
		put({ opening: true });
		const ticket = ++generation;
		if (!(await deps.settle())) {
			put({ opening: false });
			deps.notify.error('Save the elixir first.');
			return false;
		}
		const fresh = deps.node(nodeId) ?? node;
		stopWatching();
		loaded = null;
		openedRevision = deps.revision();
		openedPrint = nodeFingerprint(fresh);
		const options = deps.options();
		const label = options.find((option) => option.id === nodeId)?.label ?? fresh.title ?? 'Step';
		const followedState = deps.followed(nodeId);
		const patch = stepTabPatch(fresh, stepSettings(fresh, followedState));
		const tabId = deps.tabs.open({
			...patch,
			name: label,
			nodeId,
			persistence: follow ? 'session' : 'local',
			...(follow ? { selectedSessionId: sessionId } : {})
		});
		if (follow) {
			const tab = deps.tabs.current();
			if (tab) await deps.loadSession(tab);
		}
		if (ticket !== generation) return false;
		put({
			nodeId,
			label,
			follow,
			sessionName: follow ? followedState?.session?.name ?? fresh.session_ref?.name ?? null : null,
			edits: 0,
			opening: false,
			confirm: null,
			conflict: null
		});
		deps.host.setTitle(stepTitle(deps.elixirName(), label));
		deps.host.setDirty(0);
		if (follow) {
			unwatchSession = deps.watchSessionDirty(tabId, (dirty) => {
				if (dirty) scheduleAutosave(tabId);
			});
		} else {
			unwatch = deps.tabs.subscribe(observe);
		}
		return true;
	}

	async function resume(): Promise<boolean> {
		const tab = deps.tabs.current();
		const ref = tab?.embedded;
		if (!tab || !ref) return false;
		const node = deps.node(ref.nodeId);
		if (!node || !canEditStep(node)) {
			deps.tabs.close();
			return false;
		}
		const follow = ref.persistence === 'session';
		stopWatching();
		const label = deps.options().find((option) => option.id === ref.nodeId)?.label ?? node.title ?? 'Step';
		const baseline = ref.baseline ?? null;
		loaded = baseline ? { values: baseline.values, form: baseline.form, capturedAt: baseline.capturedAt } : null;
		openedRevision = baseline?.revision ?? deps.revision();
		openedPrint = baseline?.print ?? nodeFingerprint(node);
		generation += 1;
		put({
			nodeId: ref.nodeId,
			label,
			follow,
			sessionName: follow ? deps.followed(ref.nodeId)?.session?.name ?? node.session_ref?.name ?? null : null,
			edits: 0,
			opening: false,
			confirm: null,
			conflict: null
		});
		deps.host.setTitle(stepTitle(deps.elixirName(), label));
		if (follow) {
			unwatchSession = deps.watchSessionDirty(tab.id, (dirty) => {
				if (dirty) scheduleAutosave(tab.id);
			});
		} else {
			unwatch = deps.tabs.subscribe(observe);
		}
		return true;
	}

	function leave(saved: boolean) {
		const nodeId = current.nodeId;
		generation += 1;
		stopWatching();
		loaded = null;
		deps.tabs.close();
		deps.host.setDirty(0);
		deps.host.setTitle(deps.elixirName());
		store.set((current = IDLE_STEP));
		if (nodeId) deps.onReturn(nodeId, saved);
	}

	async function flushFollowed(): Promise<boolean> {
		const tab = deps.tabs.current();
		if (!tab) return true;
		if (autosaveHandle !== null) {
			timers.clear(autosaveHandle);
			autosaveHandle = null;
		}
		return deps.saveSession(tab.id);
	}

	async function back() {
		if (!current.nodeId || current.opening) return;
		if (current.follow) {
			if (!(await flushFollowed())) {
				deps.notify.error('The session could not be saved. Resolve it in the session first.');
				return;
			}
			leave(false);
			return;
		}
		if (current.edits > 0) {
			put({ confirm: { reason: 'leave', target: null } });
			return;
		}
		leave(false);
	}

	async function go(nodeId: string) {
		if (!current.nodeId || current.opening || nodeId === current.nodeId) return;
		if (current.follow) {
			if (!(await flushFollowed())) {
				deps.notify.error('The session could not be saved. Resolve it in the session first.');
				return;
			}
			await open(nodeId);
			return;
		}
		if (current.edits > 0) {
			put({ confirm: { reason: 'switch', target: nodeId } });
			return;
		}
		await open(nodeId);
	}

	async function save(after: After = 'leave', options: { overwrite?: boolean } = {}): Promise<CommitResult | 'noop'> {
		const nodeId = current.nodeId;
		if (!nodeId || current.follow || current.saving) return 'noop';
		const tab = deps.tabs.current();
		if (!tab) return 'noop';
		put({ saving: true, confirm: null });
		const label = current.label;
		try {
			const latest = await deps.fetchLatest();
			if (!latest) {
				deps.notify.error('The elixir could not be saved. Try again.');
				return 'error';
			}
			const theirs = latest.node(nodeId);
			if (latest.revision !== openedRevision) {
				if (!theirs) {
					put({ conflict: { reason: 'gone', canOverwrite: false, after } });
					return 'conflict';
				}
				if (nodeFingerprint(theirs) !== openedPrint && !options.overwrite) {
					const mine = deps.node(nodeId);
					put({
						conflict: {
							reason: 'changed',
							canOverwrite: theirs.preset_id === mine?.preset_id && theirs.mode === mine?.mode && sessionMode(theirs) !== 'follow',
							after
						}
					});
					return 'conflict';
				}
				deps.adopt(latest.record);
			}
			const base = deps.node(nodeId);
			if (!base) {
				put({ conflict: { reason: 'gone', canOverwrite: false, after } });
				return 'conflict';
			}
			const previous = { ...(base.overrides ?? {}) };
			const overrides = stepOverrides(base, tab, loaded);
			const changed = JSON.stringify(overrides) !== JSON.stringify(previous);
			if (changed) {
				const result = await deps.commit(nodeId, overrides);
				if (result === 'conflict') {
					put({ conflict: { reason: 'changed', canOverwrite: true, after } });
					return 'conflict';
				}
				if (result === 'error') {
					deps.notify.error('The elixir could not be saved. Try again.');
					return 'error';
				}
				openedRevision = deps.revision();
				openedPrint = nodeFingerprint(deps.node(nodeId));
				remember();
				deps.notify.saved(`Saved ${label} to ${deps.elixirName()}`, () => {
					void deps.commit(nodeId, previous);
				});
			}
			put({ saving: false });
			if (after === 'leave') leave(changed);
			else {
				stopWatching();
				await open(after.switchTo);
			}
			return 'saved';
		} finally {
			if (current.saving) put({ saving: false });
		}
	}

	async function answerConfirm(choice: 'save' | 'discard' | 'cancel') {
		const pending = current.confirm;
		if (!pending) return;
		if (choice === 'cancel') {
			put({ confirm: null });
			return;
		}
		if (choice === 'discard') {
			put({ confirm: null });
			if (pending.reason === 'leave') leave(false);
			else if (pending.target) await open(pending.target);
			return;
		}
		await save(pending.reason === 'switch' && pending.target ? { switchTo: pending.target } : 'leave');
	}

	async function resolveConflict(choice: 'cancel' | 'reload' | 'overwrite') {
		const pending = current.conflict;
		if (!pending) return;
		put({ conflict: null });
		if (choice === 'cancel') return;
		const nodeId = current.nodeId;
		if (!nodeId) return;
		if (choice === 'overwrite') {
			if (pending.canOverwrite) await save(pending.after, { overwrite: true });
			return;
		}
		const latest = await deps.fetchLatest();
		if (latest) deps.adopt(latest.record);
		if (latest?.node(nodeId)) await open(nodeId);
		else leave(false);
	}

	function destroy() {
		generation += 1;
		stopWatching();
		store.set((current = IDLE_STEP));
	}

	return {
		state: { subscribe: store.subscribe },
		snapshot: () => current,
		open,
		resume,
		go,
		back,
		save,
		answerConfirm,
		resolveConflict,
		destroy
	};
}
