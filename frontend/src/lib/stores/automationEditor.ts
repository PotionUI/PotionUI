/**
 * Automation editor state — the mapping boundary between the backend's
 * snake_case graph JSON (`AutomationGraph`) and xyflow's node/edge shapes.
 *
 * `toFlowNode`/`toFlowEdge` convert graph JSON -> xyflow; `fromFlowGraph`
 * converts back, stripping every xyflow-only field (selected, dragging,
 * measured width/height, etc.) so the save payload matches the contract
 * exactly. Dirty tracking compares the current graph (re-derived via
 * `fromFlowGraph`) against a JSON snapshot taken at load/save time.
 */
import { derived, get, writable } from 'svelte/store';
import type { Edge, Node } from '@xyflow/svelte';
import { api } from '$lib/services/api';
import { layoutGraph } from '$lib/automations/autoLayout';
import { logger } from '$lib/utils/logger';
import type {
	Automation,
	AutomationEdge,
	AutomationGraph,
	AutomationNode,
	NodeKind,
	NodeTypeDef,
	ValidationIssue
} from '$lib/types/automations';

export interface FlowNodeData extends Record<string, unknown> {
	nodeType: string;
	kind: NodeKind;
	config: Record<string, any>;
}

export type FlowNode = Node<FlowNodeData, NodeKind>;
export type FlowEdge = Edge;

/** Derive the node "kind" (trigger/condition/action) from a node-type key
 *  like "trigger.filesystem" — the prefix before the first dot. */
export function kindFromNodeType(nodeType: string): NodeKind {
	const kind = nodeType.split('.')[0];
	if (kind === 'trigger' || kind === 'condition' || kind === 'action') return kind;
	return 'action';
}

let autoId = 0;
/** Generate a reasonably-unique node id for new palette drops. */
export function generateNodeId(nodeType: string): string {
	autoId += 1;
	return `${nodeType.replace(/\./g, '_')}_${Date.now().toString(36)}_${autoId}`;
}

// ─── Graph JSON <-> xyflow mapping boundary ───────────────────────────────

export function toFlowNode(node: AutomationNode): FlowNode {
	const kind = kindFromNodeType(node.type);
	return {
		id: node.id,
		type: kind,
		position: { x: node.position?.x ?? 0, y: node.position?.y ?? 0 },
		data: {
			nodeType: node.type,
			kind,
			config: node.config ?? {}
		}
	};
}

export function toFlowEdge(edge: AutomationEdge): FlowEdge {
	return {
		id: edge.id,
		source: edge.source,
		target: edge.target,
		sourceHandle: edge.source_handle,
		targetHandle: edge.target_handle
	};
}

export function fromFlowNode(node: FlowNode): AutomationNode {
	return {
		id: node.id,
		type: node.data.nodeType,
		position: { x: node.position.x, y: node.position.y },
		config: node.data.config ?? {}
	};
}

export function fromFlowEdge(edge: FlowEdge): AutomationEdge {
	return {
		id: edge.id,
		source: edge.source,
		target: edge.target,
		source_handle: edge.sourceHandle ?? 'out',
		target_handle: edge.targetHandle ?? 'in'
	};
}

export function fromFlowGraph(nodes: FlowNode[], edges: FlowEdge[]): AutomationGraph {
	return {
		nodes: nodes.map(fromFlowNode),
		edges: edges.map(fromFlowEdge)
	};
}

/** Stable JSON snapshot of a graph, used for dirty-diffing (order-sensitive,
 *  which is fine since both sides derive from the same array order). */
function snapshot(graph: AutomationGraph): string {
	return JSON.stringify(graph);
}

// ─── Graph ancestry (drives the variable picker) ───────────────────────────

/**
 * Every node that can reach `nodeId`, nearest first.
 *
 * These are exactly the nodes whose output a node may reference: the engine
 * only populates `upstream[<id>]` for nodes it has already executed on the path
 * to this one. Cycle-guarded via the visited set (the backend rejects cycles at
 * save time, but the editor can hold one transiently while the user wires).
 */
export function getAncestorNodeIds(
	edges: Pick<FlowEdge, 'source' | 'target'>[],
	nodeId: string
): string[] {
	const parents = new Map<string, string[]>();
	for (const edge of edges) {
		const existing = parents.get(edge.target);
		if (existing) existing.push(edge.source);
		else parents.set(edge.target, [edge.source]);
	}

	const seen = new Set<string>([nodeId]);
	const ancestors: string[] = [];
	let frontier = [nodeId];

	while (frontier.length > 0) {
		const next: string[] = [];
		for (const id of frontier) {
			for (const parent of parents.get(id) ?? []) {
				if (seen.has(parent)) continue;
				seen.add(parent);
				ancestors.push(parent);
				next.push(parent);
			}
		}
		frontier = next;
	}

	return ancestors;
}

/** One insertable reference. `path` is the bare dot-path; the caller decides
 *  whether to wrap it in `{{ }}` (Jinja action field) or insert it raw
 *  (dot-path condition field). */
export interface VariableRef {
	path: string;
	key: string;
	type: string;
	description?: string;
	example?: unknown;
}

export interface UpstreamScope {
	nodeId: string;
	title: string;
	outputs: VariableRef[];
	/** The node's payload shape isn't statically knowable. */
	dynamic: boolean;
}

export interface VariableScope {
	/** Fields of `event.*`, contributed by the trigger ancestor(s). */
	event: VariableRef[];
	eventDynamic: boolean;
	upstream: UpstreamScope[];
}

/**
 * What `nodeId` may reference, derived from its ancestors' declared outputs.
 *
 * A node's `event.*` comes specifically from its TRIGGER ancestor — the engine
 * seeds `upstream[trigger_node_id] = event_payload`, and downstream nodes read
 * that payload as `event`. Every other ancestor is addressed as
 * `upstream.<node_id>.<field>`. (A trigger is also reachable as
 * `upstream.<trigger_id>`, but `event` is the idiomatic spelling, so we only
 * offer that one.)
 *
 * Pure: `defs` is passed in rather than read from the catalog store, so this is
 * unit-testable without a store.
 */
export function buildVariableScope(
	nodes: FlowNode[],
	edges: Pick<FlowEdge, 'source' | 'target'>[],
	nodeId: string,
	defs: NodeTypeDef[]
): VariableScope {
	const defByKey = new Map(defs.map((def) => [def.key, def]));
	const nodeById = new Map(nodes.map((node) => [node.id, node]));

	const event: VariableRef[] = [];
	const upstream: UpstreamScope[] = [];
	const seenEventKeys = new Set<string>();
	let eventDynamic = false;

	for (const ancestorId of getAncestorNodeIds(edges, nodeId)) {
		const node = nodeById.get(ancestorId);
		if (!node) continue;
		const def = defByKey.get(node.data.nodeType);
		if (!def) continue;

		if (def.kind === 'trigger') {
			// Several triggers can feed one node on different paths; union their
			// fields rather than arbitrarily picking one.
			if (def.dynamic_outputs) eventDynamic = true;
			for (const output of def.outputs ?? []) {
				if (seenEventKeys.has(output.key)) continue;
				seenEventKeys.add(output.key);
				event.push({ ...output, path: `event.${output.key}` });
			}
			continue;
		}

		upstream.push({
			nodeId: ancestorId,
			title: def.title ?? node.data.nodeType,
			dynamic: def.dynamic_outputs === true,
			outputs: (def.outputs ?? []).map((output) => ({
				...output,
				path: `upstream.${ancestorId}.${output.key}`
			}))
		});
	}

	return { event, eventDynamic, upstream };
}

// ─── Undo / redo (pure helpers) ────────────────────────────────────────────

export interface GraphSnapshot {
	nodes: FlowNode[];
	edges: FlowEdge[];
}

export interface History {
	past: GraphSnapshot[];
	future: GraphSnapshot[];
	/** Identifies the edit that produced the top of `past`, so a run of related
	 *  edits (typing into one config field) collapses into a single undo step. */
	lastKey: string | null;
}

export const HISTORY_CAP = 50;

export function emptyHistory(): History {
	return { past: [], future: [], lastKey: null };
}

function cloneSnapshot(present: GraphSnapshot): GraphSnapshot {
	return structuredClone({ nodes: present.nodes, edges: present.edges });
}

function sameGraph(a: GraphSnapshot, b: GraphSnapshot): boolean {
	return snapshot(fromFlowGraph(a.nodes, a.edges)) === snapshot(fromFlowGraph(b.nodes, b.edges));
}

/**
 * Record `present` as an undo point, i.e. the state to return to.
 *
 * Two things stop the stack from flooding. `NodeConfigForm`'s `onChange` fires
 * on every keystroke and its default-seeding effect fires on every node select,
 * so without these you'd get one undo entry per character typed:
 *
 * - `coalesceKey`: a run of edits sharing a key pushes only once. The first push
 *   already captured the pre-edit state, which is what undo should restore.
 * - a no-op edit (graph unchanged) is never pushed.
 *
 * Any push invalidates the redo branch, as usual.
 */
export function pushHistory(
	history: History,
	present: GraphSnapshot,
	options: { coalesceKey?: string; cap?: number } = {}
): History {
	const { coalesceKey, cap = HISTORY_CAP } = options;
	const top = history.past[history.past.length - 1];

	if (coalesceKey && history.lastKey === coalesceKey) return history;
	if (top && sameGraph(top, present)) return history;

	const past = [...history.past, cloneSnapshot(present)];
	return {
		past: past.length > cap ? past.slice(past.length - cap) : past,
		future: [],
		lastKey: coalesceKey ?? null
	};
}

/** Returns `null` when there is nothing to undo. */
export function undoHistory(
	history: History,
	present: GraphSnapshot
): { history: History; present: GraphSnapshot } | null {
	if (history.past.length === 0) return null;
	const restored = history.past[history.past.length - 1];
	return {
		history: {
			past: history.past.slice(0, -1),
			future: [cloneSnapshot(present), ...history.future],
			lastKey: null
		},
		present: restored
	};
}

/** Returns `null` when there is nothing to redo. */
export function redoHistory(
	history: History,
	present: GraphSnapshot
): { history: History; present: GraphSnapshot } | null {
	if (history.future.length === 0) return null;
	const [restored, ...future] = history.future;
	return {
		history: {
			past: [...history.past, cloneSnapshot(present)],
			future,
			lastKey: null
		},
		present: restored
	};
}

// ─── Editor store ──────────────────────────────────────────────────────────

export interface AutomationEditorState {
	automation: Automation | null;
	nodes: FlowNode[];
	edges: FlowEdge[];
	selectedNodeId: string | null;
	saving: boolean;
	validating: boolean;
	validationIssues: ValidationIssue[];
	lastSavedSnapshot: string;
	loaded: boolean;
	error: string | null;
	/** Bumped on every nodes/edges mutation. `Canvas.svelte` keys its
	 *  store -> local-`$state` resync effect on this instead of on the
	 *  `nodes`/`edges` array references themselves, since xyflow's `bind:nodes`
	 *  mutates those local arrays directly (drag/connect/delete) and syncing
	 *  on identity would ping-pong with that. Discrete graph-shape changes
	 *  (palette add/remove/config edit/load) bump it; per-frame drag deltas
	 *  (pushed to the store via `setNodes`/`setEdges` on drag-stop, not every
	 *  frame) intentionally do NOT need a resync since xyflow already owns
	 *  the local array in that case. */
	version: number;
	/** Undo/redo stack. Only graph-shape changes are recorded — never a bare
	 *  selection change (see `selectNode`). */
	history: History;
	/** Incremented by `applyAutoLayout` so `Canvas` knows to re-fit the viewport.
	 *  `version` alone can't tell a layout apart from any other graph mutation. */
	layoutTick: number;
}

function initialState(): AutomationEditorState {
	return {
		automation: null,
		nodes: [],
		edges: [],
		selectedNodeId: null,
		saving: false,
		validating: false,
		validationIssues: [],
		lastSavedSnapshot: snapshot({ nodes: [], edges: [] }),
		loaded: false,
		error: null,
		version: 0,
		history: emptyHistory(),
		layoutTick: 0
	};
}

function createAutomationEditorStore() {
	const { subscribe, set, update } = writable<AutomationEditorState>(initialState());

	return {
		subscribe,

		async load(automationId: string): Promise<void> {
			update((s) => ({ ...s, loaded: false, error: null }));
			try {
				const response = await api.getAutomation(automationId);
				if (response.success && response.data) {
					const automation = response.data;
					const nodes = automation.graph.nodes.map(toFlowNode);
					const edges = automation.graph.edges.map(toFlowEdge);
					set({
						automation,
						nodes,
						edges,
						selectedNodeId: null,
						saving: false,
						validating: false,
						validationIssues: [],
						lastSavedSnapshot: snapshot(automation.graph),
						version: 1,
						loaded: true,
						error: null,
						history: emptyHistory(),
						layoutTick: 0
					});
				} else {
					update((s) => ({ ...s, loaded: true, error: response.error || 'Failed to load automation' }));
				}
			} catch (error) {
				logger.error('Failed to load automation:', error);
				update((s) => ({ ...s, loaded: true, error: 'Failed to load automation' }));
			}
		},

		/**
		 * Push xyflow's live node array back into the store.
		 *
		 * `record: true` marks a user-meaningful change (drag-stop, delete) that
		 * should be undoable. Internal resyncs pass it falsy so we don't snapshot
		 * every intermediate write. Neither bumps `version`: xyflow already owns
		 * the local array here, and a resync would clobber its live `.selected`.
		 */
		setNodes(nodes: FlowNode[], options: { record?: boolean } = {}): void {
			update((s) => ({
				...s,
				history: options.record ? pushHistory(s.history, { nodes: s.nodes, edges: s.edges }) : s.history,
				nodes
			}));
		},

		setEdges(edges: FlowEdge[], options: { record?: boolean } = {}): void {
			update((s) => ({
				...s,
				history: options.record ? pushHistory(s.history, { nodes: s.nodes, edges: s.edges }) : s.history,
				edges
			}));
		},

		/** Sets the tracked selection AND stamps `.selected` onto the store's own
		 *  `nodes` copy (without bumping `version`, so this never triggers a
		 *  Canvas resync that would clobber xyflow's just-applied live
		 *  selection — see Canvas.svelte's doc comment). This keeps `s.nodes`
		 *  consistent with the real selection so that the *next* version-bumping
		 *  mutation (e.g. a config edit via `updateNodeConfig`, which rebuilds
		 *  `s.nodes` via `.map()`) doesn't silently drop the `.selected` flag
		 *  the user currently has active. */
		selectNode(nodeId: string | null): void {
			update((s) => ({
				...s,
				selectedNodeId: nodeId,
				nodes: s.nodes.map((n) => ({ ...n, selected: n.id === nodeId }))
			}));
		},

		/** Merge a config patch into one node's `data.config` (used by the Inspector form).
		 *  Coalesced per node, so typing a value is one undo step, not one per keystroke. */
		updateNodeConfig(nodeId: string, config: Record<string, any>): void {
			update((s) => ({
				...s,
				history: pushHistory(s.history, { nodes: s.nodes, edges: s.edges }, {
					coalesceKey: `config:${nodeId}`
				}),
				nodes: s.nodes.map((n) =>
					n.id === nodeId ? { ...n, data: { ...n.data, config } } : n
				),
				version: s.version + 1
			}));
		},

		removeNode(nodeId: string): void {
			update((s) => ({
				...s,
				history: pushHistory(s.history, { nodes: s.nodes, edges: s.edges }),
				nodes: s.nodes.filter((n) => n.id !== nodeId),
				edges: s.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
				selectedNodeId: s.selectedNodeId === nodeId ? null : s.selectedNodeId,
				version: s.version + 1
			}));
		},

		addNodeFromPalette(nodeType: NodeTypeDef, position: { x: number; y: number }): FlowNode {
			const node: FlowNode = {
				id: generateNodeId(nodeType.key),
				type: nodeType.kind,
				position,
				data: {
					nodeType: nodeType.key,
					kind: nodeType.kind,
					config: {}
				}
			};
			update((s) => ({
				...s,
				history: pushHistory(s.history, { nodes: s.nodes, edges: s.edges }),
				nodes: [...s.nodes, node],
				version: s.version + 1
			}));
			return node;
		},

		/** Reposition every node with the pure layered-DAG layout. Undoable. */
		applyAutoLayout(): void {
			update((s) => {
				const positions = layoutGraph(s.nodes, s.edges);
				return {
					...s,
					history: pushHistory(s.history, { nodes: s.nodes, edges: s.edges }),
					nodes: s.nodes.map((n) => {
						const position = positions.get(n.id);
						return position ? { ...n, position } : n;
					}),
					version: s.version + 1,
					layoutTick: s.layoutTick + 1
				};
			});
		},

		/** Restore the previous graph. Bumps `version` so Canvas resyncs xyflow's
		 *  bound arrays; drops a stale selection pointing at a node that's gone. */
		undo(): void {
			update((s) => {
				const result = undoHistory(s.history, { nodes: s.nodes, edges: s.edges });
				if (!result) return s;
				return {
					...s,
					history: result.history,
					nodes: result.present.nodes,
					edges: result.present.edges,
					selectedNodeId: result.present.nodes.some((n) => n.id === s.selectedNodeId)
						? s.selectedNodeId
						: null,
					version: s.version + 1
				};
			});
		},

		redo(): void {
			update((s) => {
				const result = redoHistory(s.history, { nodes: s.nodes, edges: s.edges });
				if (!result) return s;
				return {
					...s,
					history: result.history,
					nodes: result.present.nodes,
					edges: result.present.edges,
					selectedNodeId: result.present.nodes.some((n) => n.id === s.selectedNodeId)
						? s.selectedNodeId
						: null,
					version: s.version + 1
				};
			});
		},

		async save(): Promise<boolean> {
			const state = get({ subscribe });
			if (!state.automation) return false;
			update((s) => ({ ...s, saving: true, error: null }));
			try {
				const graph = fromFlowGraph(state.nodes, state.edges);
				const response = await api.updateAutomation(state.automation.id, { graph });
				if (response.success && response.data) {
					const automation = response.data;
					update((s) => ({
						...s,
						automation,
						saving: false,
						lastSavedSnapshot: snapshot(graph)
					}));
					return true;
				}
				update((s) => ({ ...s, saving: false, error: response.error || 'Failed to save' }));
				return false;
			} catch (error) {
				logger.error('Failed to save automation graph:', error);
				update((s) => ({ ...s, saving: false, error: 'Failed to save' }));
				return false;
			}
		},

		async validate(): Promise<ValidationIssue[]> {
			const state = get({ subscribe });
			update((s) => ({ ...s, validating: true }));
			try {
				const graph = fromFlowGraph(state.nodes, state.edges);
				const response = await api.validateGraph(graph);
				const issues = response.success && response.data ? response.data : [];
				update((s) => ({ ...s, validating: false, validationIssues: issues }));
				return issues;
			} catch (error) {
				logger.error('Failed to validate automation graph:', error);
				update((s) => ({ ...s, validating: false }));
				return [];
			}
		},

		async setEnabled(enabled: boolean): Promise<void> {
			const state = get({ subscribe });
			if (!state.automation) return;
			try {
				const response = enabled
					? await api.enableAutomation(state.automation.id)
					: await api.disableAutomation(state.automation.id);
				if (response.success && response.data) {
					update((s) => ({ ...s, automation: response.data as Automation }));
				}
			} catch (error) {
				logger.error('Failed to toggle automation enabled state:', error);
			}
		},

		reset(): void {
			set(initialState());
		}
	};
}

export const automationEditor = createAutomationEditorStore();

/** True when the current graph differs from the last-saved snapshot. */
export const isDirty = derived(automationEditor, ($state) => {
	if (!$state.automation) return false;
	const current = snapshot(fromFlowGraph($state.nodes, $state.edges));
	return current !== $state.lastSavedSnapshot;
});

export const selectedNode = derived(automationEditor, ($state) =>
	$state.nodes.find((n) => n.id === $state.selectedNodeId) ?? null
);

export const canUndo = derived(automationEditor, ($state) => $state.history.past.length > 0);
export const canRedo = derived(automationEditor, ($state) => $state.history.future.length > 0);
