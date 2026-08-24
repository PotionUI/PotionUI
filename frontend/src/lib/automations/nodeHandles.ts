/**
 * Node handle derivation — shared by every node component (and the Inspector's
 * mirrored port list) so the port logic that used to be copy-pasted between
 * `ConditionNode.svelte` and `Inspector.svelte` lives in exactly one place.
 */
import { parseDynamicPorts, type NodeTypeDef } from '$lib/types/automations';

export interface SourceHandle {
	id: string;
	label: string;
}

/** Does this node accept an incoming flow edge? Triggers start a graph, so
 *  they never have a target handle. */
export function hasTargetHandle(def: Pick<NodeTypeDef, 'kind'>): boolean {
	return def.kind !== 'trigger';
}

/** The node's outgoing flow-edge handles for THIS node instance's config.
 *
 * `id` is the wire contract — it becomes `edge.source_handle` in the saved
 * graph, so it's always returned verbatim (never uppercased/trimmed beyond
 * what `parseDynamicPorts` already does). `label` is display-only.
 */
export function getSourceHandles(
	def: NodeTypeDef,
	config: Record<string, unknown> | undefined
): SourceHandle[] {
	const dynamicKey = def.dynamic_ports_config_key;
	if (dynamicKey) {
		return parseDynamicPorts(config?.[dynamicKey]).map((id) => ({ id, label: id }));
	}

	if (def.kind === 'condition') {
		return [
			{ id: 'true', label: 'TRUE' },
			{ id: 'false', label: 'FALSE' }
		];
	}

	const ports = def.output_ports && def.output_ports.length > 0 ? def.output_ports : ['out'];
	return ports.map((id) => ({ id, label: id.toUpperCase() }));
}
