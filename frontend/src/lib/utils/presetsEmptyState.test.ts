import { describe, it, expect } from 'vitest';
import { describePresetsEmptyState } from './presetsEmptyState';
import type { ReadinessReport } from '$lib/services/api';

function report(overrides: Partial<ReadinessReport['checks'][number]>[]): ReadinessReport {
	const base: ReadinessReport['checks'] = [
		{ area: 'service', status: 'ready', code: 'SERVICE_OK', message: 'The service is running.', action: null },
		{ area: 'execution', status: 'ready', code: 'EXECUTION_READY', message: 'Generation is available.', action: null },
		{ area: 'content', status: 'ready', code: 'CONTENT_READY', message: 'You have presets ready to generate with.', action: null },
		{
			area: 'generation_proven',
			status: 'not_ready',
			code: 'NO_GENERATION_YET',
			message: 'No generation has finished on this instance yet. Run one to finish setup.',
			action: null
		}
	];
	for (const patch of overrides) {
		const row = base.find((r) => r.area === patch.area);
		if (row) Object.assign(row, patch);
	}
	return { overall: 'not_ready', checks: base };
}

describe('describePresetsEmptyState', () => {
	it('falls back to a generic message when readiness has not loaded yet', () => {
		const admin = describePresetsEmptyState(null, true);
		const user = describePresetsEmptyState(null, false);

		expect(admin.kind).toBe('unknown');
		expect(admin.showSetupLink).toBe(true);
		expect(admin.action).toBeNull();
		expect(user.showSetupLink).toBe(false);
		expect(user.message).toMatch(/ask your administrator/i);
	});

	it('surfaces NO_PRESETS_ASSIGNED as user-not-assigned, with admin action gated to admins', () => {
		const readiness = report([
			{
				area: 'content',
				status: 'not_ready',
				code: 'NO_PRESETS_ASSIGNED',
				message: "You don't have any presets yet. Ask your administrator to assign one.",
				action: null
			}
		]);
		// Admin phrasing carries the repair action (readiness role-resolves this
		// server-side; simulate the admin row here).
		readiness.checks.find((c) => c.area === 'content')!.action =
			'Open Administration -> Presets, install a preset and assign it to this user.';

		const forUser = describePresetsEmptyState(readiness, false);
		expect(forUser.kind).toBe('user-not-assigned');
		expect(forUser.action).toBeNull();
		expect(forUser.showSetupLink).toBe(false);
		expect(forUser.message).toBe("You don't have any presets yet. Ask your administrator to assign one.");

		const forAdmin = describePresetsEmptyState(readiness, true);
		expect(forAdmin.kind).toBe('user-not-assigned');
		expect(forAdmin.showSetupLink).toBe(true);
		expect(forAdmin.action).toBe('Open Administration -> Presets, install a preset and assign it to this user.');
	});

	it('prefers the content facet over execution when both are blocking', () => {
		const readiness = report([
			{ area: 'execution', status: 'not_ready', code: 'NO_EXECUTION_BACKEND', message: 'no backend', action: 'fix backend' },
			{ area: 'content', status: 'not_ready', code: 'NO_PRESETS_ASSIGNED', message: 'no presets', action: 'fix presets' }
		]);
		const result = describePresetsEmptyState(readiness, true);
		expect(result.message).toBe('no presets');
		expect(result.action).toBe('fix presets');
	});

	it('falls back to execution when content is ready but execution is not', () => {
		const readiness = report([
			{ area: 'execution', status: 'not_ready', code: 'NO_EXECUTION_BACKEND', message: 'no backend', action: 'fix backend' }
		]);
		const result = describePresetsEmptyState(readiness, true);
		expect(result.kind).toBe('owner-unconfigured');
		expect(result.message).toBe('no backend');
		expect(result.action).toBe('fix backend');
	});

	it('treats a degraded content facet (presets without usable models) as owner-unconfigured', () => {
		const readiness = report([
			{
				area: 'content',
				status: 'degraded',
				code: 'PRESETS_WITHOUT_MODELS',
				message: "Your presets don't have any usable models yet. Ask your administrator.",
				action: 'Index models on the matching backend and assign the models to this user.'
			}
		]);
		const result = describePresetsEmptyState(readiness, true);
		expect(result.kind).toBe('owner-unconfigured');
		expect(result.showSetupLink).toBe(true);
	});

	it('falls back to the generic message when readiness reports nothing blocking', () => {
		const readiness = report([]);
		const result = describePresetsEmptyState(readiness, false);
		expect(result.kind).toBe('unknown');
		expect(result.message).toMatch(/ask your administrator/i);
	});
});
