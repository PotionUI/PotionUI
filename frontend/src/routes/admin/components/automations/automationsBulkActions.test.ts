import { describe, it, expect } from 'vitest';
import type { Automation } from '$lib/types/automations';
import { idsNeedingDisable, idsNeedingEnable } from './automationsBulkActions';

function automation(overrides: Partial<Automation> = {}): Automation {
	return {
		id: 'a1',
		name: 'Tag new LoRAs',
		enabled: false,
		graph: { nodes: [], edges: [] },
		version: 1,
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

describe('idsNeedingEnable / idsNeedingDisable', () => {
	const disabled = automation({ id: 'disabled-1', enabled: false });
	const enabled = automation({ id: 'enabled-1', enabled: true });
	const automations = [disabled, enabled];

	it('idsNeedingEnable only returns selected, currently-disabled ids', () => {
		expect(idsNeedingEnable(automations, new Set(['disabled-1', 'enabled-1']))).toEqual(['disabled-1']);
	});

	it('idsNeedingDisable only returns selected, currently-enabled ids', () => {
		expect(idsNeedingDisable(automations, new Set(['disabled-1', 'enabled-1']))).toEqual(['enabled-1']);
	});

	it('ignores ids outside the selection', () => {
		expect(idsNeedingEnable(automations, new Set(['enabled-1']))).toEqual([]);
		expect(idsNeedingDisable(automations, new Set(['disabled-1']))).toEqual([]);
	});
});
