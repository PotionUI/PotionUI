import { describe, it, expect } from 'vitest';
import { currentRunStepKey } from './recipeCurrentStep';
import type { SetupRunStepView } from '$lib/services/api/setup';

function step(overrides: Partial<SetupRunStepView> = {}): SetupRunStepView {
	return {
		step_key: 'artifacts_fetch',
		title: 'Download models',
		kind: 'artifacts.fetch',
		ordinal: 0,
		status: 'pending',
		attempts: [],
		...overrides
	};
}

describe('currentRunStepKey', () => {
	it('is null when there is no run', () => {
		expect(currentRunStepKey(null)).toBeNull();
	});

	it('is null when no step is in progress yet', () => {
		expect(
			currentRunStepKey({
				steps: [step({ status: 'pending' }), step({ step_key: 'preset_ensure', status: 'succeeded' })],
				attempts: []
			})
		).toBeNull();
	});

	it('finds the running step', () => {
		expect(
			currentRunStepKey({
				steps: [
					step({ step_key: 'plugins_ensure', status: 'succeeded' }),
					step({ step_key: 'artifacts_fetch', status: 'running' }),
					step({ step_key: 'preset_ensure', status: 'pending' })
				],
				attempts: []
			})
		).toBe('artifacts_fetch');
	});

	it('finds a step awaiting consent', () => {
		expect(
			currentRunStepKey({
				steps: [step({ step_key: 'artifacts_fetch', status: 'awaiting_consent' })],
				attempts: []
			})
		).toBe('artifacts_fetch');
	});
});
