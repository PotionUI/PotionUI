import { isAxiosError } from 'axios';
import type { SetupRunStatus } from '$lib/services/api/setup';

export interface ActiveRecipeRunConflict {
	message: string;
	activeRun: {
		id: string;
		recipeId: string;
		recipeName: string;
		status: SetupRunStatus;
		currentStepKey: string | null;
	};
}

interface ActiveRunConflictBody {
	detail?: {
		message?: string;
		active_run?: {
			id?: string;
			recipe_id?: string;
			recipe_name?: string;
			status?: string;
			current_step_key?: string | null;
		};
	};
}

export function parseActiveRecipeRunConflict(error: unknown): ActiveRecipeRunConflict | null {
	if (!isAxiosError<ActiveRunConflictBody>(error) || error.response?.status !== 409) return null;
	const detail = error.response.data?.detail;
	const activeRun = detail?.active_run;
	if (!activeRun || typeof activeRun.id !== 'string' || typeof activeRun.recipe_id !== 'string' || typeof activeRun.status !== 'string') {
		return null;
	}
	return {
		message: typeof detail?.message === 'string' ? detail.message : 'Another recipe run is already in progress.',
		activeRun: {
			id: activeRun.id,
			recipeId: activeRun.recipe_id,
			recipeName: typeof activeRun.recipe_name === 'string' ? activeRun.recipe_name : activeRun.recipe_id,
			status: activeRun.status as SetupRunStatus,
			currentStepKey: typeof activeRun.current_step_key === 'string' ? activeRun.current_step_key : null
		}
	};
}
