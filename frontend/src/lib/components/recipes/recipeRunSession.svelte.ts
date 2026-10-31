import { api } from '$lib/services/api/index';
import type { SetupRun } from '$lib/services/api/setup';
import { getApiErrorMessage, logger } from '$lib/utils/logger';
import { isRunTerminal, shouldPollRun, RUN_POLL_INTERVAL_MS } from '$lib/utils/setupRunDisplay';
import { parseActiveRecipeRunConflict, type ActiveRecipeRunConflict } from '$lib/utils/recipeRunConflict';
import { createVisiblePoll, type VisiblePoll } from '$lib/utils/visiblePoll';

export interface RecipeRunApi {
	get(runId: string): Promise<SetupRun>;
	create?(recipeId: string, preselectModes?: string[]): Promise<SetupRun>;
	cancel?(runId: string): Promise<unknown>;
}

export interface RecipeRunSessionOptions {
	onFinished?: (run: SetupRun) => void;
	onRun?: (run: SetupRun | null) => void;
	onPollError?: (error: unknown) => 'drop' | 'retry';
	api?: RecipeRunApi;
}

const recipeRunApi: RecipeRunApi = {
	create: (recipeId, preselectModes) =>
		preselectModes?.length ? api.startRecipeModesRun(recipeId, preselectModes) : api.createRecipeRun(recipeId),
	get: (runId) => api.getRecipeRun(runId),
	cancel: (runId) => api.applyRecipeRunAction(runId, 'cancel')
};

export class RecipeRunSession {
	run = $state<SetupRun | null>(null);
	starting = $state(false);
	error = $state('');
	conflict = $state<ActiveRecipeRunConflict | null>(null);
	#poll: VisiblePoll;
	#options: RecipeRunSessionOptions;
	#api: RecipeRunApi;
	#finishedRunIds = new Set<string>();

	constructor(options: RecipeRunSessionOptions = {}) {
		this.#options = options;
		this.#api = options.api ?? recipeRunApi;
		this.#poll = createVisiblePoll(() => this.#refresh(), RUN_POLL_INTERVAL_MS);
	}

	get inFlight(): boolean {
		return !!this.run && !isRunTerminal(this.run.status);
	}

	async start(recipeId: string, preselectModes?: string[]): Promise<void> {
		const create = this.#requireApi('create');
		if (this.starting || this.inFlight) return;
		this.starting = true;
		this.error = '';
		this.conflict = null;
		try {
			this.adopt(await (preselectModes?.length ? create(recipeId, preselectModes) : create(recipeId)));
		} catch (error) {
			const conflict = parseActiveRecipeRunConflict(error);
			if (conflict && conflict.activeRun.recipeId === recipeId) {
				await this.#attach(conflict.activeRun.id);
			} else if (conflict) {
				this.conflict = conflict;
			} else {
				this.error = getApiErrorMessage(error, 'Could not start this recipe');
			}
		} finally {
			this.starting = false;
		}
	}

	async cancelConflict(): Promise<void> {
		if (!this.conflict) return;
		const cancel = this.#requireApi('cancel');
		const runId = this.conflict.activeRun.id;
		try {
			await cancel(runId);
			this.conflict = null;
		} catch (error) {
			this.error = getApiErrorMessage(error, 'Could not cancel the other run');
		}
	}

	#requireApi<K extends 'create' | 'cancel'>(name: K): NonNullable<RecipeRunApi[K]> {
		const fn = this.#api[name];
		if (!fn) throw new Error(`Recipe run api has no ${name}`);
		return fn.bind(this.#api) as NonNullable<RecipeRunApi[K]>;
	}

	async #attach(runId: string): Promise<void> {
		try {
			this.adopt(await this.#api.get(runId));
		} catch (error) {
			this.error = getApiErrorMessage(error, 'Could not attach to the running recipe');
		}
	}

	adopt = (updated: SetupRun): void => {
		this.run = updated;
		this.#options.onRun?.(updated);
		if (shouldPollRun(updated.status)) {
			this.#poll.start();
			return;
		}
		this.#poll.stop();
		if (isRunTerminal(updated.status) && !this.#finishedRunIds.has(updated.id)) {
			this.#finishedRunIds.add(updated.id);
			this.#options.onFinished?.(updated);
		}
	};

	reset(): void {
		this.#poll.stop();
		this.run = null;
		this.error = '';
		this.conflict = null;
		this.starting = false;
		this.#options.onRun?.(null);
	}

	dispose(): void {
		this.#poll.stop();
	}

	async #refresh(): Promise<void> {
		const runId = this.run?.id;
		if (!runId) return;
		try {
			const fetched = await this.#api.get(runId);
			if (this.run?.id !== runId) return;
			this.adopt(fetched);
		} catch (error) {
			if (this.run?.id !== runId) return;
			if (this.#options.onPollError?.(error) === 'drop') {
				this.reset();
				return;
			}
			logger.warn('Recipe run poll failed', runId, error);
		}
	}
}
