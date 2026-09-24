import { describe, it, expect } from 'vitest';
import { parseActiveRecipeRunConflict } from './recipeRunConflict';

function axiosError(status: number, data: unknown) {
	return { isAxiosError: true, message: 'Request failed', response: { status, data } };
}

describe('parseActiveRecipeRunConflict', () => {
	it('reads the structured 409 active-run payload', () => {
		const err = axiosError(409, {
			detail: {
				message: 'Krea-2 Starter is already running on this instance.',
				active_run: {
					id: 'run-1',
					recipe_id: 'krea2-starter',
					recipe_name: 'Krea-2 Starter',
					status: 'awaiting_consent',
					current_step_key: 'download_model'
				}
			}
		});

		expect(parseActiveRecipeRunConflict(err)).toEqual({
			message: 'Krea-2 Starter is already running on this instance.',
			activeRun: {
				id: 'run-1',
				recipeId: 'krea2-starter',
				recipeName: 'Krea-2 Starter',
				status: 'awaiting_consent',
				currentStepKey: 'download_model'
			}
		});
	});

	it('falls back to the recipe id when recipe_name is missing', () => {
		const err = axiosError(409, {
			detail: { active_run: { id: 'run-1', recipe_id: 'krea2-starter', status: 'pending' } }
		});

		expect(parseActiveRecipeRunConflict(err)?.activeRun.recipeName).toBe('krea2-starter');
	});

	it('returns null for a non-409 error', () => {
		const err = axiosError(400, { detail: { active_run: { id: 'run-1', recipe_id: 'r', status: 'pending' } } });
		expect(parseActiveRecipeRunConflict(err)).toBeNull();
	});

	it('returns null for a 409 without the structured active_run payload', () => {
		const err = axiosError(409, 'Another recipe run is already in progress on this instance.');
		expect(parseActiveRecipeRunConflict(err)).toBeNull();
	});

	it('returns null for a non-axios error', () => {
		expect(parseActiveRecipeRunConflict(new Error('nope'))).toBeNull();
	});
});
