/**
 * How much detail an automation node card renders.
 *
 * This is *view* state, deliberately kept out of `automationEditor`: changing it
 * must never bump that store's `version` counter, which is what drives the
 * xyflow resync (see `Canvas.svelte`). Nodes read it directly rather than
 * receiving it as a prop, because xyflow constructs custom node components with
 * a fixed prop set.
 *
 * Big graphs default to `compact` — a wall of full cards is unreadable — but an
 * explicit toggle wins from then on. Driving this from live zoom instead would
 * re-render every node on every wheel tick.
 */
import { derived, writable } from 'svelte/store';

export type NodeDetail = 'full' | 'compact';

/** Node count past which a graph auto-collapses to compact cards. */
export const AUTO_COMPACT_THRESHOLD = 12;

/** `null` = follow the node-count heuristic; a value = the user chose. */
const override = writable<NodeDetail | null>(null);
const nodeCount = writable(0);

export const nodeDetail = derived([override, nodeCount], ([$override, $count]) =>
	$override ?? ($count > AUTO_COMPACT_THRESHOLD ? 'compact' : 'full')
);

export function setNodeCount(count: number): void {
	nodeCount.set(count);
}

export function toggleNodeDetail(current: NodeDetail): void {
	override.set(current === 'full' ? 'compact' : 'full');
}

/** Drop the manual choice and follow the node-count heuristic again. */
export function resetNodeDetail(): void {
	override.set(null);
}
