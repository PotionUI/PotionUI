import { api } from '$lib/services/api/index';
import type { SetupRun } from '$lib/services/api/setup';
import { getApiErrorMessage, logger } from '$lib/utils/logger';
import { isRunTerminal, shouldPollRun, RUN_POLL_INTERVAL_MS } from '$lib/utils/setupRunDisplay';

export class RecipeRunSession {
	run = $state<SetupRun | null>(null);
	starting = $state(false);
	error = $state('');
	#timer: ReturnType<typeof setTimeout> | null = null;
	#onFinished: ((run: SetupRun) => void) | undefined;

	constructor(options: { onFinished?: (run: SetupRun) => void } = {}) {
		this.#onFinished = options.onFinished;
	}

	get inFlight(): boolean {
		return !!this.run && !isRunTerminal(this.run.status);
	}

	async start(recipeId: string): Promise<void> {
		if (this.starting || this.inFlight) return;
		this.starting = true;
		this.error = '';
		try {
			this.adopt(await api.createRecipeRun(recipeId));
		} catch (error) {
			this.error = getApiErrorMessage(error, 'Could not start this recipe');
		} finally {
			this.starting = false;
		}
	}

	adopt = (updated: SetupRun): void => {
		this.run = updated;
		if (shouldPollRun(updated.status)) this.#schedule(updated.id);
		else this.#clear();
	};

	reset(): void {
		this.#clear();
		this.run = null;
		this.error = '';
		this.starting = false;
	}

	dispose(): void {
		this.#clear();
	}

	#clear(): void {
		if (this.#timer) {
			clearTimeout(this.#timer);
			this.#timer = null;
		}
	}

	#schedule(runId: string): void {
		this.#clear();
		this.#timer = setTimeout(() => void this.#refresh(runId), RUN_POLL_INTERVAL_MS);
	}

	async #refresh(runId: string): Promise<void> {
		try {
			const fetched = await api.getRecipeRun(runId);
			if (this.run?.id !== runId) return;
			this.adopt(fetched);
			if (isRunTerminal(fetched.status)) this.#onFinished?.(fetched);
		} catch (error) {
			logger.warn('Recipe run poll failed', runId, error);
			if (this.run?.id === runId) this.#schedule(runId);
		}
	}
}
