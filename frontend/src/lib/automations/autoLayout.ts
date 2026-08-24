/**
 * Pure, dependency-free layered DAG layout for the automation graph editor.
 * Left-to-right (nodes have input handles on the left, outputs on the right).
 *
 * No xyflow/Svelte/DOM imports — this module only computes positions from
 * an abstract node/edge list so it can be unit tested and reused outside the
 * editor component.
 */

export interface LayoutNode {
	id: string;
}

export interface LayoutEdge {
	source: string;
	target: string;
}

export interface LayoutOptions {
	nodeWidth?: number;
	nodeHeight?: number;
	hGap?: number;
	vGap?: number;
}

const DEFAULT_NODE_WIDTH = 260;
const DEFAULT_NODE_HEIGHT = 130;
const DEFAULT_H_GAP = 90;
const DEFAULT_V_GAP = 40;

/** Deterministic string comparator used for every tie-break in this module. */
function byId(a: string, b: string): number {
	return a < b ? -1 : a > b ? 1 : 0;
}

export function layoutGraph(
	nodes: LayoutNode[],
	edges: LayoutEdge[],
	opts?: LayoutOptions
): Map<string, { x: number; y: number }> {
	const nodeWidth = opts?.nodeWidth ?? DEFAULT_NODE_WIDTH;
	const nodeHeight = opts?.nodeHeight ?? DEFAULT_NODE_HEIGHT;
	const hGap = opts?.hGap ?? DEFAULT_H_GAP;
	const vGap = opts?.vGap ?? DEFAULT_V_GAP;

	const result = new Map<string, { x: number; y: number }>();
	if (nodes.length === 0) return result;

	const nodeIds = new Set(nodes.map((n) => n.id));
	// Edges referencing a node that doesn't exist are ignored rather than crashing —
	// the graph may be mid-edit when layout runs.
	const validEdges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

	const outgoing = new Map<string, string[]>();
	const incoming = new Map<string, string[]>();
	const indegree = new Map<string, number>();
	for (const id of nodeIds) {
		outgoing.set(id, []);
		incoming.set(id, []);
		indegree.set(id, 0);
	}
	for (const e of validEdges) {
		outgoing.get(e.source)!.push(e.target);
		incoming.get(e.target)!.push(e.source);
		indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1);
	}

	// --- 1. Longest-path layering via Kahn's algorithm ------------------------
	// layer(v) = max(layer(u) + 1) over all incoming edges u -> v. Because a node
	// is only enqueued once every incoming edge has been relaxed, its layer value
	// is final by the time it's dequeued — the dequeue order itself doesn't
	// affect correctness, only which nodes are ready first.
	const layer = new Map<string, number>();
	const remainingIndegree = new Map(indegree);
	const queue: string[] = [];
	for (const id of nodeIds) {
		if (indegree.get(id) === 0) {
			layer.set(id, 0);
			queue.push(id);
		}
	}
	queue.sort(byId);

	let head = 0;
	while (head < queue.length) {
		const id = queue[head++];
		const curLayer = layer.get(id)!;
		for (const target of outgoing.get(id)!) {
			layer.set(target, Math.max(layer.get(target) ?? 0, curLayer + 1));
			const rem = (remainingIndegree.get(target) ?? 0) - 1;
			remainingIndegree.set(target, rem);
			if (rem === 0) queue.push(target);
		}
	}

	// --- 2. Cycle safety --------------------------------------------------------
	// Presets are validated as acyclic server-side, but the editor can transiently
	// hold a cycle mid-edit (e.g. while dragging a new edge). Any node Kahn's
	// algorithm never resolved (indegree never reached 0) is placed one layer past
	// everything that *could* be resolved, in deterministic id order. This
	// guarantees termination and that every node gets a position, even though the
	// layering within the cycle itself isn't meaningful.
	let maxResolvedLayer = -1;
	for (const l of layer.values()) maxResolvedLayer = Math.max(maxResolvedLayer, l);
	const unresolved = [...nodeIds].filter((id) => !layer.has(id)).sort(byId);
	for (const id of unresolved) layer.set(id, maxResolvedLayer + 1);

	// Group node ids by layer, sorted by id for a deterministic starting order
	// (independent of input node array order or Kahn processing order).
	const layers: string[][] = [];
	for (const [id, l] of layer) {
		(layers[l] ??= []).push(id);
	}
	for (const arr of layers) arr.sort(byId);

	// --- 3. Barycenter ordering within each layer --------------------------------
	// Reduces edge crossings: each node's position within its layer is nudged
	// toward the mean position of its neighbours in the adjacent layer. A few
	// down/up sweeps let ordering information propagate across the whole graph;
	// the heuristic doesn't guarantee monotonic improvement so we cap the sweep
	// count instead of iterating to convergence.
	const orderIndex = new Map<string, number>();
	const reindex = () => {
		orderIndex.clear();
		for (const arr of layers) arr.forEach((id, i) => orderIndex.set(id, i));
	};
	reindex();

	const sortByBarycenter = (arr: string[], neighboursOf: Map<string, string[]>) => {
		const bary = new Map<string, number>();
		for (const id of arr) {
			const neighbours = (neighboursOf.get(id) ?? []).filter((n) => orderIndex.has(n));
			bary.set(
				id,
				neighbours.length === 0
					? orderIndex.get(id)!
					: neighbours.reduce((sum, n) => sum + orderIndex.get(n)!, 0) / neighbours.length
			);
		}
		arr.sort((a, b) => bary.get(a)! - bary.get(b)! || byId(a, b));
	};

	const SWEEP_PASSES = 2; // 2 down+up passes = 4 sweeps total
	for (let pass = 0; pass < SWEEP_PASSES; pass++) {
		for (let l = 1; l < layers.length; l++) {
			if (layers[l]) sortByBarycenter(layers[l], incoming);
			reindex();
		}
		for (let l = layers.length - 2; l >= 0; l--) {
			if (layers[l]) sortByBarycenter(layers[l], outgoing);
			reindex();
		}
	}

	// --- 4. Coordinates -----------------------------------------------------------
	const layerHeight = (arr: string[] | undefined) =>
		arr && arr.length > 0 ? arr.length * nodeHeight + (arr.length - 1) * vGap : 0;
	const maxHeight = Math.max(0, ...layers.map(layerHeight));

	for (let l = 0; l < layers.length; l++) {
		const arr = layers[l];
		if (!arr) continue;
		// Centre each layer vertically about the tallest layer's midpoint.
		const offsetY = (maxHeight - layerHeight(arr)) / 2;
		arr.forEach((id, i) => {
			result.set(id, {
				x: l * (nodeWidth + hGap),
				y: offsetY + i * (nodeHeight + vGap)
			});
		});
	}

	return result;
}
