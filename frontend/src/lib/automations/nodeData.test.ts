import { describe, expect, it } from 'vitest';
import {
	getDataInputs,
	outputPrefix,
	outputRef,
	outputTemplate,
	referencesRunData
} from './nodeData';
import type { NodeTypeDef } from '$lib/types/automations';

const def = (properties: Record<string, any>, kind: 'trigger' | 'condition' | 'action' = 'action') =>
	({
		key: 'x',
		kind,
		title: 'X',
		output_ports: ['out'],
		config_schema: { properties }
	}) as unknown as NodeTypeDef;

describe('referencesRunData', () => {
	it('recognises a Jinja template', () => {
		expect(referencesRunData('{{ event.path }}')).toBe(true);
	});

	it('recognises a bare dot-path', () => {
		expect(referencesRunData('event.rel_parts.0')).toBe(true);
		expect(referencesRunData('upstream.idx_1.model_id')).toBe(true);
	});

	it('treats a literal as unbound', () => {
		expect(referencesRunData('krea2')).toBe(false);
		expect(referencesRunData('')).toBe(false);
		expect(referencesRunData(undefined)).toBe(false);
		expect(referencesRunData(42)).toBe(false);
	});

	it('does not mistake a word merely containing "event" for a reference', () => {
		expect(referencesRunData('prevent.disaster')).toBe(false);
		expect(referencesRunData('my_event.thing')).toBe(false);
	});
});

describe('getDataInputs', () => {
	it('lists only fields that read run data, not the whole config form', () => {
		// Mirrors trigger.filesystem: none of its settings read upstream data.
		const inputs = getDataInputs(
			def({
				pattern: { type: 'string', title: 'Filename Pattern' },
				debounce_ms: { type: 'number', title: 'Debounce (ms)' }
			}),
			{}
		);
		expect(inputs).toEqual([]);
	});

	it('picks up a templatable action field', () => {
		const inputs = getDataInputs(
			def({
				path: { type: 'string', title: 'File Path', templatable: true, default: '{{ event.path }}' },
				model_type: { type: 'string', title: 'Model Type' }
			}),
			{}
		);
		expect(inputs).toHaveLength(1);
		expect(inputs[0]).toMatchObject({
			name: 'path',
			accepts: 'template',
			hint: '{{ … }}',
			value: '{{ event.path }}',
			bound: true
		});
	});

	it('picks up a condition dot-path field', () => {
		const inputs = getDataInputs(
			def({
				field: { type: 'string', title: 'Field', input_ref: 'path' },
				cases: { type: 'textbox', title: 'Cases' }
			}, 'condition'),
			{ field: 'event.rel_parts.0' }
		);
		expect(inputs).toEqual([
			{
				name: 'field',
				title: 'Field',
				accepts: 'path',
				hint: 'dot-path',
				value: 'event.rel_parts.0',
				bound: true
			}
		]);
	});

	it('picks up an expression field', () => {
		const inputs = getDataInputs(
			def({ expression: { type: 'string', title: 'Expression', input_ref: 'expression' } }, 'condition'),
			{ expression: 'event.size > 0' }
		);
		expect(inputs[0].accepts).toBe('expression');
		expect(inputs[0].bound).toBe(true);
	});

	it('marks a data field holding a literal as unbound', () => {
		const inputs = getDataInputs(
			def({ tag_name: { type: 'string', title: 'Tag Name', templatable: true } }),
			{ tag_name: 'krea2' }
		);
		expect(inputs[0]).toMatchObject({ value: 'krea2', bound: false });
	});

	it('reports an empty data field as having no value', () => {
		const inputs = getDataInputs(
			def({ model_id: { type: 'string', title: 'Model ID', templatable: true, default: '' } }),
			{ model_id: '   ' }
		);
		expect(inputs[0]).toMatchObject({ value: null, bound: false });
	});

	it('falls back to the field default when config has no value', () => {
		const inputs = getDataInputs(
			def({ path: { type: 'string', templatable: true, default: '{{ event.path }}' } }),
			undefined
		);
		expect(inputs[0].value).toBe('{{ event.path }}');
	});

	it('is safe with an unknown node type', () => {
		expect(getDataInputs(undefined, {})).toEqual([]);
	});
});

describe('outputPrefix / outputRef', () => {
	it("a trigger's payload is reachable as event.*", () => {
		expect(outputPrefix('trigger', 'fs_1')).toBe('event');
		expect(outputRef('trigger', 'fs_1', 'path')).toBe('event.path');
	});

	it('every other node is reachable as upstream.<node_id>.*', () => {
		expect(outputPrefix('action', 'idx_1')).toBe('upstream.idx_1');
		expect(outputRef('action', 'idx_1', 'model_id')).toBe('upstream.idx_1.model_id');
		expect(outputRef('condition', 'sw_1', 'value')).toBe('upstream.sw_1.value');
	});

	it('wraps a reference for a Jinja-templated field', () => {
		expect(outputTemplate('trigger', 'fs_1', 'path')).toBe('{{ event.path }}');
		expect(outputTemplate('action', 'idx_1', 'model_id')).toBe('{{ upstream.idx_1.model_id }}');
	});
});
