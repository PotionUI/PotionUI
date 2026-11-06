// @vitest-environment jsdom
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

describe('RecipeRunSession.start / onFinished', () => {
	it('calls onFinished immediately when the created run is already terminal', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'completed' }) as never);
		const onFinished = vi.fn();
		const session = new RecipeRunSession({ onFinished });
		sessions.push(session);

		await session.start('krea2-starter');

		expect(onFinished).toHaveBeenCalledTimes(1);
		expect(onFinished).toHaveBeenCalledWith(expect.objectContaining({ id: 'run-1', status: 'completed' }));
	});

	it('does not call onFinished while the run is still in flight', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'pending' }) as never);
		const onFinished = vi.fn();
		const session = new RecipeRunSession({ onFinished });
		sessions.push(session);

		await session.start('krea2-starter');

		expect(onFinished).not.toHaveBeenCalled();
	});

	it('does not call onFinished twice for the same run adopted terminal more than once', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'completed' }) as never);
		const onFinished = vi.fn();
		const session = new RecipeRunSession({ onFinished });
		sessions.push(session);

		await session.start('krea2-starter');
		session.adopt(run({ status: 'completed' }) as never);

		expect(onFinished).toHaveBeenCalledTimes(1);
	});
});

describe('RecipeRunSession without create or cancel', () => {
	it('throws from start and cancelConflict instead of reporting a soft error', async () => {
		const get = vi.fn();
		const session = new RecipeRunSession({ api: { get } });
		sessions.push(session);

		await expect(session.start('krea2-starter')).rejects.toThrow('no create');
		expect(session.error).toBe('');

		session.conflict = {
			message: 'busy',
			activeRun: { id: 'run-9', recipeId: 'x', recipeName: 'X', status: 'running', currentStepKey: null }
		};
		await expect(session.cancelConflict()).rejects.toThrow('no cancel');
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

function setHidden(hidden: boolean) {
	Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

describe('RecipeRunSession polling', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		setHidden(false);
	});

	afterEach(() => {
		vi.useRealTimers();
		setHidden(false);
	});

	it('polls until the run is terminal, fires onFinished once and stops', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'running' }) as never);
		vi.mocked(api.getRecipeRun)
			.mockResolvedValueOnce(run({ status: 'running' }) as never)
			.mockResolvedValue(run({ status: 'completed' }) as never);
		const onFinished = vi.fn();
		const session = new RecipeRunSession({ onFinished });
		sessions.push(session);

		await session.start('krea2-starter');
		await vi.advanceTimersByTimeAsync(2500);
		expect(onFinished).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(2500);
		expect(onFinished).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(10000);

		expect(api.getRecipeRun).toHaveBeenCalledTimes(2);
		expect(onFinished).toHaveBeenCalledTimes(1);
	});

	it('pauses while the tab is hidden and refreshes immediately when it returns', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'running' }) as never);
		vi.mocked(api.getRecipeRun).mockResolvedValue(run({ status: 'running' }) as never);
		const session = makeSession();
		await session.start('krea2-starter');

		setHidden(true);
		await vi.advanceTimersByTimeAsync(10000);
		expect(api.getRecipeRun).not.toHaveBeenCalled();

		setHidden(false);
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(0);
		expect(api.getRecipeRun).toHaveBeenCalledTimes(1);
	});

	it('keeps polling after a failed refresh', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(run({ status: 'running' }) as never);
		vi.mocked(api.getRecipeRun)
			.mockRejectedValueOnce(new Error('blip'))
			.mockResolvedValue(run({ status: 'completed' }) as never);
		const session = makeSession();
		await session.start('krea2-starter');

		await vi.advanceTimersByTimeAsync(5000);

		expect(session.run?.status).toBe('completed');
	});

	it('uses the injected api, reports every run to onRun and drops a run the poll error handler gives up on', async () => {
		const create = vi.fn().mockResolvedValue(run({ status: 'running' }));
		const get = vi.fn().mockRejectedValue({ status: 404 });
		const onRun = vi.fn();
		const session = new RecipeRunSession({
			api: { create, get, cancel: vi.fn() },
			onRun,
			onPollError: (error) => ((error as { status?: number }).status === 404 ? 'drop' : 'retry')
		});
		sessions.push(session);

		await session.start('krea2-starter');
		expect(create).toHaveBeenCalledWith('krea2-starter');
		expect(onRun).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(2500);
		expect(get).toHaveBeenCalledWith('run-1');
		expect(session.run).toBeNull();
		expect(onRun).toHaveBeenLastCalledWith(null);
		await vi.advanceTimersByTimeAsync(10000);
		expect(get).toHaveBeenCalledTimes(1);
		expect(api.createRecipeRun).not.toHaveBeenCalled();
	});

	it('forwards the modes a door preselected to create', async () => {
		const create = vi.fn().mockResolvedValue(run({ status: 'awaiting_consent' }));
		const session = new RecipeRunSession({ api: { create, get: vi.fn(), cancel: vi.fn() } });
		sessions.push(session);

		await session.start('modes-ltx-2-5', ['cinemagraph']);
		expect(create).toHaveBeenCalledWith('modes-ltx-2-5', ['cinemagraph']);
	});
});
