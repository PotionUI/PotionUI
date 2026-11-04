import type { ElixirChange } from './events';

export type RemoteAction = 'ignore' | 'run' | 'adopt' | 'followed' | 'conflict';

export interface RemoteState {
	id: string;
	runId: string | null;
	saveState: string;
	revision: number | null;
}

export function remoteAction(change: ElixirChange | null | undefined, state: RemoteState): RemoteAction {
	if (!change || change.elixir_id !== state.id) return 'ignore';
	if (change.run_id) return change.run_id === state.runId ? 'run' : 'ignore';
	if (state.saveState === 'saved') return 'adopt';
	if (change.revision !== null && change.revision !== undefined && change.revision === state.revision) return 'followed';
	return 'conflict';
}
