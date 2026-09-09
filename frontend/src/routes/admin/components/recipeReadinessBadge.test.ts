import { describe, it, expect } from 'vitest';
import { deriveRecipeReadiness } from './recipeReadinessBadge';
import type { ReadinessReport, ReadinessStatus } from '$lib/services/api/setup';

function report(overrides: Partial<Record<string, ReadinessStatus>> = {}): ReadinessReport {
	const checks: ReadinessReport['checks'] = [
		{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: '', action: null },
		{ area: 'execution', status: 'ready', code: 'EXECUTION_READY', message: '', action: null },
		{ area: 'content', status: 'ready', code: 'CONTENT_READY', message: '', action: null },
		{ area: 'generation_proven', status: 'ready', code: 'GENERATED', message: '', action: null }
	];
	for (const row of checks) {
		const patched = overrides[row.area];
		if (patched) row.status = patched;
	}
	const overall: ReadinessStatus = checks.every((c) => c.status === 'ready') ? 'ready' : 'not_ready';
	return { overall, checks };
}

describe('deriveRecipeReadiness', () => {
	it('reports unknown when readiness has not been fetched', () => {
		const badge = deriveRecipeReadiness(null);
		expect(badge.kind).toBe('unknown');
		expect(badge.label).toBe('Unknown');
		expect(badge.variant).toBe('neutral');
	});

	it('reports unknown for a malformed report rather than throwing', () => {
		expect(deriveRecipeReadiness({ overall: 'ready' } as ReadinessReport).kind).toBe('unknown');
	});

	it('reports ready when every facet is ready', () => {
		const badge = deriveRecipeReadiness(report());
		expect(badge.kind).toBe('ready');
		expect(badge.variant).toBe('success');
	});

	it('reports missing models when the content facet is blocking', () => {
		const badge = deriveRecipeReadiness(report({ content: 'not_ready' }));
		expect(badge.kind).toBe('missing-models');
		expect(badge.label).toBe('Missing models');
	});

	it('treats a degraded content facet as missing models', () => {
		expect(deriveRecipeReadiness(report({ content: 'degraded' })).kind).toBe('missing-models');
	});

	it('reports needs backend when the execution facet is blocking', () => {
		const badge = deriveRecipeReadiness(report({ execution: 'not_ready' }));
		expect(badge.kind).toBe('needs-backend');
		expect(badge.variant).toBe('danger');
	});

	it('prefers needs backend over missing models when both are blocking', () => {
		expect(deriveRecipeReadiness(report({ execution: 'not_ready', content: 'not_ready' })).kind).toBe(
			'needs-backend'
		);
	});

	it('still reads as ready when only an unrepairable facet is blocking', () => {
		// `generation_proven` clears when someone generates, not when a recipe
		// runs — it must not make every row look broken.
		expect(deriveRecipeReadiness(report({ generation_proven: 'not_ready' })).kind).toBe('ready');
	});
});
