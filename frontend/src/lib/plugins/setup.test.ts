import { describe, it, expect } from 'vitest';
import { nextSetupStep, setupMapFrom, setupPendingFor, setupProgressLabel, type PluginSetupReport } from './setup';

function report(overrides: Partial<PluginSetupReport> = {}): PluginSetupReport {
	return {
		plugin_id: 'fake-provider',
		complete: false,
		remaining: 2,
		steps: [
			{ id: '0-backend.added', kind: 'backend.added', label: 'Add a backend', description: '', status: 'done' },
			{ id: '1-cloud.models_enabled', kind: 'cloud.models_enabled', label: 'Turn on models', description: '', status: 'todo' },
			{ id: '2-presets.assigned', kind: 'presets.assigned', label: 'Assign', description: '', status: 'waiting' }
		],
		...overrides
	};
}

describe('plugin setup helpers', () => {
	it('keys reports by plugin id', () => {
		const map = setupMapFrom([report(), report({ plugin_id: 'other' })]);
		expect(Object.keys(map).sort()).toEqual(['fake-provider', 'other']);
	});

	it('names the first step still to do, skipping waiting ones', () => {
		expect(nextSetupStep(report())?.id).toBe('1-cloud.models_enabled');
		const waitingFirst = report({
			steps: [
				{ id: 'a', kind: 'x', label: 'A', description: '', status: 'waiting' },
				{ id: 'b', kind: 'y', label: 'B', description: '', status: 'todo' }
			]
		});
		expect(nextSetupStep(waitingFirst)?.id).toBe('b');
		expect(nextSetupStep(report({ complete: true, remaining: 0, steps: [] }))).toBeNull();
	});

	it('counts done steps for the progress label', () => {
		expect(setupProgressLabel(report())).toBe('1 of 3 done');
	});

	it('only flags enabled plugins whose setup is incomplete', () => {
		const map = setupMapFrom([report(), report({ plugin_id: 'done', complete: true, remaining: 0 })]);
		expect(setupPendingFor({ id: 'fake-provider', enabled: true }, map)?.plugin_id).toBe('fake-provider');
		expect(setupPendingFor({ id: 'fake-provider', enabled: false }, map)).toBeNull();
		expect(setupPendingFor({ id: 'done', enabled: true }, map)).toBeNull();
		expect(setupPendingFor({ id: 'unknown', enabled: true }, map)).toBeNull();
	});
});
