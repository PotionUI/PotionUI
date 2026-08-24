import type { ReadinessArea, ReadinessReport, ReadinessStatus } from '$lib/services/api/setup';

/** Badge variant per readiness status - semantic tokens only. */
export function readinessBadgeVariant(status: ReadinessStatus): 'success' | 'warning' | 'danger' {
	if (status === 'ready') return 'success';
	if (status === 'degraded') return 'warning';
	return 'danger';
}

/** Human label for a readiness facet card. */
export function readinessAreaLabel(area: ReadinessArea): string {
	switch (area) {
		case 'service':
			return 'Service';
		case 'execution':
			return 'Generation backend';
		case 'content':
			return 'Presets & models';
		case 'generation_proven':
			return 'First generation';
	}
}

/**
 * Where an admin lands when they follow a not-ready card's action, for the
 * three facets whose repair happens on another page. `service` has no page -
 * its action is a server-side check/restart - so it returns null.
 */
export function readinessAdminLink(area: ReadinessArea): string | null {
	switch (area) {
		case 'execution':
			return '/admin?tab=backends';
		case 'content':
			return '/admin?tab=presets';
		case 'generation_proven':
			return '/generate';
		case 'service':
			return null;
	}
}

/** Plain-words overall headline for the setup home. */
export function readinessHeadline(report: ReadinessReport): string {
	if (report.overall === 'ready') return 'Everything works';
	const outstanding = report.checks.filter((check) => check.status !== 'ready').length;
	if (outstanding === 0) return 'Almost there';
	const noun = outstanding === 1 ? 'thing needs' : 'things need';
	return `Almost there — ${outstanding} ${noun} attention`;
}
