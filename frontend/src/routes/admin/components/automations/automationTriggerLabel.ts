import { kindFromNodeType } from '$lib/stores/automationEditor';
import type { AutomationGraph, NodeTypeDef } from '$lib/types/automations';

function titleCase(value: string): string {
	return value
		.replace(/[_.]/g, ' ')
		.trim()
		.replace(/\b\w/g, (char) => char.toUpperCase());
}

export function triggerLabel(graph: AutomationGraph, nodeTypes: readonly NodeTypeDef[]): string {
	const node = graph.nodes.find((n) => kindFromNodeType(n.type) === 'trigger');
	if (!node) return 'No trigger';
	const def = nodeTypes.find((t) => t.key === node.type);
	if (def) return def.title;
	const bareKey = node.type.split('.').slice(1).join(' ') || node.type;
	return titleCase(bareKey);
}
