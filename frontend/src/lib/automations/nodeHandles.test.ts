import { describe, expect, it } from 'vitest';
import { getSourceHandles, hasTargetHandle } from './nodeHandles';
import type { NodeTypeDef } from '$lib/types/automations';

function makeDef(overrides: Partial<NodeTypeDef>): NodeTypeDef {
	return {
		key: 'test.node',
		kind: 'action',
		title: 'Test Node',
		config_schema: { properties: {} },
		output_ports: ['out'],
		...overrides
	};
}

describe('hasTargetHandle', () => {
	it('trigger nodes have no target handle', () => {
		expect(hasTargetHandle({ kind: 'trigger' })).toBe(false);
	});

	it('action nodes have a target handle', () => {
		expect(hasTargetHandle({ kind: 'action' })).toBe(true);
	});

	it('condition nodes have a target handle', () => {
		expect(hasTargetHandle({ kind: 'condition' })).toBe(true);
	});
});

describe('getSourceHandles', () => {
	it('trigger node exposes a single "out" source handle', () => {
		const def = makeDef({ kind: 'trigger', output_ports: ['out'] });
		expect(getSourceHandles(def, undefined)).toEqual([{ id: 'out', label: 'OUT' }]);
	});

	it('action node exposes a single "out" source handle', () => {
		const def = makeDef({ kind: 'action', output_ports: ['out'] });
		expect(getSourceHandles(def, {})).toEqual([{ id: 'out', label: 'OUT' }]);
	});

	it('static condition node exposes true/false handles with uppercase labels', () => {
		const def = makeDef({ kind: 'condition', output_ports: [] });
		const handles = getSourceHandles(def, {});
		expect(handles).toEqual([
			{ id: 'true', label: 'TRUE' },
			{ id: 'false', label: 'FALSE' }
		]);
	});

	it('ids are preserved verbatim (lowercase) while labels are display-cased', () => {
		const def = makeDef({ kind: 'condition', output_ports: [] });
		const [trueHandle] = getSourceHandles(def, {});
		expect(trueHandle.id).toBe('true');
		expect(trueHandle.label).toBe('TRUE');
		expect(trueHandle.id).not.toBe(trueHandle.label);
	});

	it('dynamic switch node derives handles from config, always ending with "default"', () => {
		const def = makeDef({
			kind: 'condition',
			output_ports: [],
			dynamic_ports_config_key: 'cases'
		});
		const handles = getSourceHandles(def, { cases: 'loras, checkpoints' });
		expect(handles).toEqual([
			{ id: 'loras', label: 'loras' },
			{ id: 'checkpoints', label: 'checkpoints' },
			{ id: 'default', label: 'default' }
		]);
	});

	it('dynamic switch node still yields the trailing "default" handle when cases is empty/undefined', () => {
		const defEmpty = makeDef({
			kind: 'condition',
			output_ports: [],
			dynamic_ports_config_key: 'cases'
		});
		expect(getSourceHandles(defEmpty, { cases: '' })).toEqual([{ id: 'default', label: 'default' }]);
		expect(getSourceHandles(defEmpty, undefined)).toEqual([{ id: 'default', label: 'default' }]);
	});

	it('missing output_ports falls back to a single "out" handle', () => {
		const def = makeDef({ kind: 'action', output_ports: [] as unknown as string[] });
		delete (def as Partial<NodeTypeDef>).output_ports;
		expect(getSourceHandles(def, {})).toEqual([{ id: 'out', label: 'OUT' }]);
	});

	it('empty output_ports falls back to a single "out" handle', () => {
		const def = makeDef({ kind: 'action', output_ports: [] });
		expect(getSourceHandles(def, {})).toEqual([{ id: 'out', label: 'OUT' }]);
	});

	it('config undefined does not throw', () => {
		const def = makeDef({ kind: 'action', output_ports: ['out'] });
		expect(() => getSourceHandles(def, undefined)).not.toThrow();
	});
});
