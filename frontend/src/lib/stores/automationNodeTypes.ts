/**
 * Node-type palette catalog (`GET /api/automations/node-types`).
 *
 * Loaded once per editor session and grouped by kind (trigger/condition/action)
 * for the NodePalette. Plugin-extensible on the backend, so this always
 * re-fetches unless `force` is false and a load has already happened.
 */
import { derived, writable } from 'svelte/store';
import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type { NodeKind, NodeTypeDef } from '$lib/types/automations';

function createAutomationNodeTypesStore() {
	const { subscribe, set } = writable<NodeTypeDef[]>([]);
	let loaded = false;
	let loading = false;

	return {
		subscribe,

		async load(force = false): Promise<void> {
			if (loading) return;
			if (loaded && !force) return;
			loading = true;
			try {
				const response = await api.listNodeTypes();
				if (response.success && response.data) {
					set(response.data);
					loaded = true;
				}
			} catch (error) {
				logger.error('Failed to load automation node types:', error);
			} finally {
				loading = false;
			}
		},

		reset(): void {
			loaded = false;
			set([]);
		}
	};
}

export const automationNodeTypes = createAutomationNodeTypesStore();

/** Node-type catalog grouped by kind, for the NodePalette's sections. */
export const groupedByKind = derived(automationNodeTypes, ($types) => {
	const groups: Record<NodeKind, NodeTypeDef[]> = { trigger: [], condition: [], action: [] };
	for (const nodeType of $types) {
		if (!groups[nodeType.kind]) groups[nodeType.kind] = [];
		groups[nodeType.kind].push(nodeType);
	}
	return groups;
});

export function findNodeTypeDef(
	types: NodeTypeDef[],
	key: string
): NodeTypeDef | undefined {
	return types.find((t) => t.key === key);
}
