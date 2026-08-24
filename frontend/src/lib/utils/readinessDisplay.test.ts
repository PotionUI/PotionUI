import { describe, it, expect } from 'vitest';
import {
	readinessBadgeVariant,
	readinessAreaLabel,
	readinessAdminLink,
	readinessHeadline
} from './readinessDisplay';
import type { ReadinessCheck, ReadinessReport } from '$lib/services/api/setup';

function check(overrides: Partial<ReadinessCheck> = {}): ReadinessCheck {
	return {
		area: 'service',
		status: 'ready',
		code: 'SERVICE_OK',
		message: 'ok',
		action: null,
		...overrides
	};
}

describe('readinessBadgeVariant', () => {
	it('maps ready/degraded/not_ready to success/warning/danger', () => {
		expect(readinessBadgeVariant('ready')).toBe('success');
		expect(readinessBadgeVariant('degraded')).toBe('warning');
		expect(readinessBadgeVariant('not_ready')).toBe('danger');
	});
});

describe('readinessAreaLabel', () => {
	it('gives every area a distinct human label', () => {
		const labels = new Set(
			(['service', 'execution', 'content', 'generation_proven'] as const).map(readinessAreaLabel)
		);
		expect(labels.size).toBe(4);
	});
});

describe('readinessAdminLink', () => {
	it('links execution/content/generation_proven to their admin surfaces', () => {
		expect(readinessAdminLink('execution')).toBe('/admin?tab=backends');
		expect(readinessAdminLink('content')).toBe('/admin?tab=presets');
		expect(readinessAdminLink('generation_proven')).toBe('/generate');
	});

	it('has no page link for service (its fix is server-side)', () => {
		expect(readinessAdminLink('service')).toBeNull();
	});
});

describe('readinessHeadline', () => {
	it('is the friendly all-clear when overall is ready', () => {
		const report: ReadinessReport = { overall: 'ready', checks: [check(), check()] };
		expect(readinessHeadline(report)).toBe('Everything works');
	});

	it('counts the non-ready facets, singular', () => {
		const report: ReadinessReport = {
			overall: 'not_ready',
			checks: [check(), check({ area: 'execution', status: 'not_ready' })]
		};
		expect(readinessHeadline(report)).toBe('Almost there — 1 thing needs attention');
	});

	it('counts the non-ready facets, plural, mixing degraded and not_ready', () => {
		const report: ReadinessReport = {
			overall: 'not_ready',
			checks: [
				check({ area: 'execution', status: 'not_ready' }),
				check({ area: 'content', status: 'degraded' })
			]
		};
		expect(readinessHeadline(report)).toBe('Almost there — 2 things need attention');
	});
});
