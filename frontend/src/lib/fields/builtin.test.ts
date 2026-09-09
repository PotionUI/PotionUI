import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn(),
	getPluginRevision: () => 'rev-1'
}));

import { registerBuiltinFieldComponents } from './builtin';
import { resolveFieldComponent } from './registry';
import SelectField from '$lib/components/form-fields/SelectField.svelte';

describe('fields/builtin', () => {
	it('registers sampler and schedule onto the same component select uses', async () => {
		registerBuiltinFieldComponents();

		const [select, sampler, schedule] = await Promise.all([
			resolveFieldComponent('select'),
			resolveFieldComponent('sampler'),
			resolveFieldComponent('schedule')
		]);

		expect(sampler).toBe(SelectField);
		expect(schedule).toBe(SelectField);
		expect(sampler).toBe(select);
		expect(schedule).toBe(select);
	});
});
