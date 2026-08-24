/**
 * Automation run history + live per-node run status.
 *
 * `applyRunUpdate` is a pure reducer (state, WS message) -> state so it can be
 * unit tested without a live socket. The store wraps it for the WS service to
 * call into (see `$lib/services/automationRunsWebsocket.ts`).
 */
import { derived, writable } from 'svelte/store';
import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type {
	AutomationRun,
	AutomationRunDetail,
	AutomationRunUpdateMessage,
	NodeRunStatus,
	RunStatus
} from '$lib/types/automations';

export interface AutomationRunsState {
	/** Run history for the automation currently open in the editor. */
	runs: AutomationRun[];
	runsLoading: boolean;
	/** The run currently being tracked live (most recent WS run-level message). */
	activeRunId: string | null;
	activeRunStatus: RunStatus | null;
	activeRunError: string | null;
	/** node_id -> status, scoped to `activeRunId`. */
	nodeStatuses: Record<string, NodeRunStatus>;
	/** Detail of a run explicitly opened from the history panel (may differ from active). */
	inspectedRun: AutomationRunDetail | null;
}

export function initialAutomationRunsState(): AutomationRunsState {
	return {
		runs: [],
		runsLoading: false,
		activeRunId: null,
		activeRunStatus: null,
		activeRunError: null,
		nodeStatuses: {},
		inspectedRun: null
	};
}

/**
 * Pure reducer: apply one `/ws/automations` `automation_run_update` message to
 * the current runs state and return the next state. Node-level messages
 * (`node_id` present) update `nodeStatuses`; run-level messages update
 * `activeRunStatus`/`activeRunError` and patch the matching row in `runs`.
 * Switching to a different `run_id` resets `nodeStatuses`.
 */
export function applyRunUpdate(
	state: AutomationRunsState,
	message: AutomationRunUpdateMessage
): AutomationRunsState {
	const isNewRun = message.run_id !== state.activeRunId;
	const nodeStatuses = isNewRun ? {} : state.nodeStatuses;

	if (message.node_id) {
		return {
			...state,
			activeRunId: message.run_id,
			nodeStatuses: { ...nodeStatuses, [message.node_id]: message.status as NodeRunStatus }
		};
	}

	const runStatus = message.status as RunStatus;
	return {
		...state,
		activeRunId: message.run_id,
		activeRunStatus: runStatus,
		activeRunError: message.error ?? null,
		nodeStatuses,
		runs: state.runs.map((r) =>
			r.id === message.run_id ? { ...r, status: runStatus, error: message.error ?? r.error } : r
		)
	};
}

function createAutomationRunsStore() {
	const { subscribe, set, update } = writable<AutomationRunsState>(initialAutomationRunsState());

	return {
		subscribe,

		async loadRuns(automationId: string): Promise<void> {
			update((s) => ({ ...s, runsLoading: true }));
			try {
				const response = await api.listRuns(automationId, { limit: 50 });
				update((s) => ({
					...s,
					runsLoading: false,
					runs: response.success && response.data ? response.data : s.runs
				}));
			} catch (error) {
				logger.error('Failed to load automation runs:', error);
				update((s) => ({ ...s, runsLoading: false }));
			}
		},

		async inspectRun(automationId: string, runId: string): Promise<void> {
			try {
				const response = await api.getRun(automationId, runId);
				if (response.success && response.data) {
					update((s) => ({ ...s, inspectedRun: response.data as AutomationRunDetail }));
				}
			} catch (error) {
				logger.error('Failed to load automation run detail:', error);
			}
		},

		/** Dispatch a WS message through the pure reducer. */
		applyWsMessage(message: AutomationRunUpdateMessage): void {
			update((s) => applyRunUpdate(s, message));
		},

		clearInspectedRun(): void {
			update((s) => ({ ...s, inspectedRun: null }));
		},

		reset(): void {
			set(initialAutomationRunsState());
		}
	};
}

export const automationRuns = createAutomationRunsStore();

export const nodeStatuses = derived(automationRuns, ($s) => $s.nodeStatuses);
export const activeRunStatus = derived(automationRuns, ($s) => $s.activeRunStatus);
