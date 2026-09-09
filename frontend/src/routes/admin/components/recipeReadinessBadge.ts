import type { ReadinessReport } from '$lib/services/api/setup';
import type { BadgeVariant } from '$lib/utils/setupRunDisplay';

/**
 * What a recipe row's readiness badge says. Only the two facets a recipe can
 * actually repair are distinguished: `execution` (no backend to run on) and
 * `content` (presets/models missing). A report that is not overall-ready for
 * some other reason — most commonly `generation_proven`, which only clears
 * once someone has generated — is still "ready" as far as installing this
 * recipe's models goes, so it reads as ready rather than inventing a fourth
 * state the admin can't act on here.
 */
export type RecipeReadinessKind = 'ready' | 'missing-models' | 'needs-backend' | 'unknown';

export interface RecipeReadinessBadge {
	kind: RecipeReadinessKind;
	label: string;
	variant: BadgeVariant;
}

const BADGES: Record<RecipeReadinessKind, RecipeReadinessBadge> = {
	ready: { kind: 'ready', label: 'Ready', variant: 'success' },
	'missing-models': { kind: 'missing-models', label: 'Missing models', variant: 'warning' },
	'needs-backend': { kind: 'needs-backend', label: 'Needs backend', variant: 'danger' },
	unknown: { kind: 'unknown', label: 'Unknown', variant: 'neutral' }
};

/**
 * Derive a row badge from a recipe-scoped readiness report. `null` (not
 * fetched yet, or the fetch failed) is "unknown" rather than a guess.
 *
 * A missing backend outranks missing models: nothing this recipe downloads is
 * usable until there is something to run it on.
 */
export function deriveRecipeReadiness(report: ReadinessReport | null): RecipeReadinessBadge {
	if (!report || !Array.isArray(report.checks)) return BADGES.unknown;

	const blocking = (area: string) =>
		report.checks.some((check) => check.area === area && check.status !== 'ready');

	if (blocking('execution')) return BADGES['needs-backend'];
	if (blocking('content')) return BADGES['missing-models'];
	return BADGES.ready;
}
