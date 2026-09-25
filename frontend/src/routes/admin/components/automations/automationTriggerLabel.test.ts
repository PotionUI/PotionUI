import { describe, it, expect } from 'vitest';
import type { AutomationGraph, NodeTypeDef } from '$lib/types/automations';
import { triggerLabel } from './automationTriggerLabel';

function graph(nodeType: string | null): AutomationGraph {
	return {
		nodes: nodeType ? [{ id: 'n1', type: nodeType, position: { x: 0, y: 0 }, config: {} }] : [],
		edges: []
	};
}

function nodeTypeDef(overrides: Partial<NodeTypeDef> = {}): NodeTypeDef {
	return {
		key: 'trigger.filesystem',
		kind: 'trigger',
		title: 'Filesystem watch',
		config_schema: { properties: {} },
		output_ports: ['out'],
		...overrides
	};
}

describe('triggerLabel', () => {
	it('returns "No trigger" when the graph has no trigger node', () => {
		expect(triggerLabel(graph(null), [])).toBe('No trigger');
	});

	it('returns the catalog title when the node type is known', () => {
		const g = graph('trigger.filesystem');
		expect(triggerLabel(g, [nodeTypeDef()])).toBe('Filesystem watch');
	});

	it('falls back to a formatted key when the catalog has not loaded', () => {
		const g = graph('trigger.hook_event');
		expect(triggerLabel(g, [])).toBe('Hook Event');
	});

	it('ignores non-trigger nodes when looking for the trigger', () => {
		const g: AutomationGraph = {
			nodes: [
				{ id: 'a1', type: 'action.add_tag', position: { x: 0, y: 0 }, config: {} },
				{ id: 't1', type: 'trigger.manual', position: { x: 0, y: 0 }, config: {} }
			],
			edges: []
		};
		expect(triggerLabel(g, [])).toBe('Manual');
	});
});
