import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		createRecipeRun: vi.fn(),
		getRecipeRun: vi.fn(),
		applyRecipeRunAction: vi.fn()
	}
}));

import { api } from '$lib/services/api/index';
import { RecipeRunSession } from './recipeRunSession.svelte';

function run(overrides: Record<string, unknown> = {}) {
	return {
		id: 'run-1',
		recipe_id: 'krea2-starter',
		recipe_version: 1,
		scope: 'instance',
		mode: 'admin',
		status: 'pending',
		current_step: null,
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps: [],
		attempts: [],
		...overrides
	};
}

function conflict409(activeRun: Record<string, unknown>) {
	return {
		isAxiosError: true,
		message: 'Request failed',
		response: { status: 409, data: { detail: { message: 'busy', active_run: activeRun } } }
	};
}

let sessions: RecipeRunSession[] = [];

function makeSession(): RecipeRunSession {
	const session = new RecipeRunSession();
	sessions.push(session);
	return session;
}

beforeEach(() => {
	vi.clearAllMocks();
	sessions = [];
});

afterEach(() => {
	for (const session of sessions) session.dispose();
});

describe('RecipeRunSession.start', () => {
	it('adopts the created run on success', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run() as never);
		const session = makeSession();

		await session.start('krea2-starter');

		expect(session.run?.id).toBe('run-1');
		expect(session.error).toBe('');
		expect(session.conflict).toBeNull();
	});

	it('attaches to the active run instead of erroring when it is the same recipe', async () => {
		vi.mocked(api.createRecipeRun).mockRejectedValue(
			conflict409({ id: 'run-9', recipe_id: 'krea2-starter', recipe_name: 'Krea-2 Starter', status: 'running' })
		);
		vi.mocked(api.getRecipeRun).mockResolvedValue(run({ id: 'run-9', status: 'running' }) as never);
		const session = makeSession();

		await session.start('krea2-starter');

		expect(api.getRecipeRun).toHaveBeenCalledWith('run-9');
		expect(session.run?.id).toBe('run-9');
		expect(session.error).toBe('');
		expect(session.conflict).toBeNull();
	});

	it('surfaces a conflict notice instead of a bare error for a different recipe', async () => {
		vi.mocked(api.createRecipeRun).mockRejectedValue(
			conflict409({ id: 'run-9', recipe_id: 'minimax-h3-starter', recipe_name: 'MiniMax-H3 Starter', status: 'awaiting_consent' })
		);
		const session = makeSession();

		await session.start('krea2-starter');

		expect(api.getRecipeRun).not.toHaveBeenCalled();
		expect(session.run).toBeNull();
		expect(session.error).toBe('');
		expect(session.conflict).toEqual({
			message: 'busy',
			activeRun: {
				id: 'run-9',
				recipeId: 'minimax-h3-starter',
				recipeName: 'MiniMax-H3 Starter',
				status: 'awaiting_consent',
				currentStepKey: null
			}
		});
	});

	it('falls back to a plain error message for a non-conflict failure', async () => {
		vi.mocked(api.createRecipeRun).mockRejectedValue(new Error('network down'));
		const session = makeSession();

		await session.start('krea2-starter');

		expect(session.conflict).toBeNull();
		expect(session.error).toBe('network down');
	});
});

describe('RecipeRunSession.cancelConflict', () => {
	it('cancels the blocking run and clears the conflict', async () => {
		vi.mocked(api.createRecipeRun).mockRejectedValue(
			conflict409({ id: 'run-9', recipe_id: 'minimax-h3-starter', recipe_name: 'MiniMax-H3 Starter', status: 'running' })
		);
		vi.mocked(api.applyRecipeRunAction).mockResolvedValue(run({ id: 'run-9', status: 'cancelled' }) as never);
		const session = makeSession();
		await session.start('krea2-starter');

		await session.cancelConflict();

		expect(api.applyRecipeRunAction).toHaveBeenCalledWith('run-9', 'cancel');
		expect(session.conflict).toBeNull();
	});
});
