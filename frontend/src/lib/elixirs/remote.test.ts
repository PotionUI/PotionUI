import { describe, expect, it } from 'vitest';
import { remoteAction } from './remote';

const state = { id: 'x1', runId: 'r1', saveState: 'saved', revision: 4 };

describe('remoteAction', () => {
	it('ignores changes to other elixirs and to nothing', () => {
		expect(remoteAction(null, state)).toBe('ignore');
		expect(remoteAction({ elixir_id: 'x2', revision: 5 }, state)).toBe('ignore');
	});

	it('reloads the shown brew when outputs were picked in it', () => {
		expect(remoteAction({ elixir_id: 'x1', revision: 4, run_id: 'r1' }, state)).toBe('run');
		expect(remoteAction({ elixir_id: 'x1', revision: 4, run_id: 'r0' }, state)).toBe('ignore');
	});

	it('takes the saved elixir when nothing is waiting to be saved', () => {
		expect(remoteAction({ elixir_id: 'x1', revision: 5 }, state)).toBe('adopt');
		expect(remoteAction({ elixir_id: 'x1', revision: 4 }, state)).toBe('adopt');
	});

	it('keeps unsaved edits: a session-only change refreshes the followed steps, a new revision is a conflict', () => {
		const dirty = { ...state, saveState: 'dirty' };
		expect(remoteAction({ elixir_id: 'x1', revision: 4 }, dirty)).toBe('followed');
		expect(remoteAction({ elixir_id: 'x1', revision: 5 }, dirty)).toBe('conflict');
	});
});
