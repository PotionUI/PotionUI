import { resolveStepGroups } from '$lib/utils/setupRunDisplay';
import type { RecipeRun } from '$lib/services/api/recipes';

const ACTIVE_STEP_STATUSES = new Set(['running', 'awaiting_consent', 'action_required']);

export function currentRunStepKey(run: Pick<RecipeRun, 'steps' | 'attempts'> | null): string | null {
	if (!run) return null;
	const { groups } = resolveStepGroups(run);
	const active = groups.find((group) => ACTIVE_STEP_STATUSES.has(group.status));
	return active?.stepKey ?? null;
}
