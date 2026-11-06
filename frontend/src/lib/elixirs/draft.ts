import { choiceLacksOption } from '$lib/utils/variableDefs';
import type { ElixirDefinition } from './types';

export function holdUnsavableVariables(draft: ElixirDefinition, saved: ElixirDefinition | null): ElixirDefinition {
	let nodes = draft.nodes;
	draft.nodes.forEach((node, index) => {
		if (node.kind !== 'llm' || !node.variables) return;
		const held = Object.keys(node.variables).filter((name) => choiceLacksOption(node.variables?.[name]));
		if (!held.length) return;
		const before = saved?.nodes.find((item) => item.id === node.id)?.variables ?? {};
		const variables = { ...node.variables };
		for (const name of held) {
			if (name in before && !choiceLacksOption(before[name])) variables[name] = before[name];
			else delete variables[name];
		}
		if (nodes === draft.nodes) nodes = [...draft.nodes];
		nodes[index] = { ...node, variables };
	});
	return nodes === draft.nodes ? draft : { ...draft, nodes };
}
