import { describe, expect, it } from 'vitest';
import {
	buildVariableScope,
	emptyHistory,
	fromFlowEdge,
	fromFlowGraph,
	fromFlowNode,
	getAncestorNodeIds,
	kindFromNodeType,
	pushHistory,
	redoHistory,
	toFlowEdge,
	toFlowNode,
	undoHistory,
	type FlowEdge,
	type FlowNode,
	type GraphSnapshot,
	type History
} from './automationEditor';
import type { AutomationGraph, NodeTypeDef } from '$lib/types/automations';

describe('kindFromNodeType', () => {
	it('derives the kind from the node-type key prefix', () => {
		expect(kindFromNodeType('trigger.filesystem')).toBe('trigger');
		expect(kindFromNodeType('condition.compare')).toBe('condition');
		expect(kindFromNodeType('action.add_tag')).toBe('action');
	});

	it('falls back to "action" for an unrecognized prefix', () => {
		expect(kindFromNodeType('mystery.thing')).toBe('action');
	});
});

describe('toFlowNode / fromFlowNode', () => {
	it('maps a graph node to an xyflow node and back losslessly', () => {
		const node = {
			id: 'n1',
			type: 'trigger.filesystem',
			position: { x: 10, y: 20 },
			config: { watch_dir: 'models/loras', event: 'created' }
		};

		const flowNode = toFlowNode(node);
		expect(flowNode.id).toBe('n1');
		expect(flowNode.type).toBe('trigger');
		expect(flowNode.position).toEqual({ x: 10, y: 20 });
		expect(flowNode.data.nodeType).toBe('trigger.filesystem');
		expect(flowNode.data.kind).toBe('trigger');
		expect(flowNode.data.config).toEqual(node.config);

		expect(fromFlowNode(flowNode)).toEqual(node);
	});

	it('strips xyflow-only fields (selected, dragging, measured) on the way back', () => {
		const node = {
			id: 'n2',
			type: 'action.add_tag',
			position: { x: 0, y: 0 },
			config: {}
		};
		const flowNode = {
			...toFlowNode(node),
			selected: true,
			dragging: true,
			measured: { width: 200, height: 80 },
			zIndex: 5
		} as any;

		expect(fromFlowNode(flowNode)).toEqual(node);
	});
});

describe('toFlowEdge / fromFlowEdge', () => {
	it('maps snake_case handles to xyflow camelCase and back', () => {
		const edge = {
			id: 'e1',
			source: 'n1',
			source_handle: 'true',
			target: 'n2',
			target_handle: 'in'
		};

		const flowEdge = toFlowEdge(edge);
		expect(flowEdge).toEqual({
			id: 'e1',
			source: 'n1',
			target: 'n2',
			sourceHandle: 'true',
			targetHandle: 'in'
		});

		expect(fromFlowEdge(flowEdge)).toEqual(edge);
	});

	it('defaults to "out"/"in" handles when xyflow omits them', () => {
		const flowEdge = { id: 'e2', source: 'n1', target: 'n2' };
		expect(fromFlowEdge(flowEdge)).toEqual({
			id: 'e2',
			source: 'n1',
			target: 'n2',
			source_handle: 'out',
			target_handle: 'in'
		});
	});
});

describe('fromFlowGraph', () => {
	it('round-trips a full graph (nodes + edges) through the xyflow boundary', () => {
		const graph: AutomationGraph = {
			nodes: [
				{
					id: 'n1',
					type: 'trigger.filesystem',
					position: { x: 0, y: 0 },
					config: { watch_dir: 'models/loras' }
				},
				{
					id: 'n2',
					type: 'condition.path_match',
					position: { x: 200, y: 0 },
					config: { pattern: 'krea' }
				},
				{
					id: 'n3',
					type: 'action.add_tag',
					position: { x: 400, y: -50 },
					config: { tag: 'krea' }
				},
				{
					id: 'n4',
					type: 'action.index_model',
					position: { x: 400, y: 50 },
					config: {}
				}
			],
			edges: [
				{ id: 'e1', source: 'n1', source_handle: 'out', target: 'n2', target_handle: 'in' },
				{ id: 'e2', source: 'n2', source_handle: 'true', target: 'n3', target_handle: 'in' },
				{ id: 'e3', source: 'n2', source_handle: 'false', target: 'n4', target_handle: 'in' }
			]
		};

		const flowNodes = graph.nodes.map(toFlowNode);
		const flowEdges = graph.edges.map(toFlowEdge);

		// Simulate xyflow decorating the runtime objects with its own bookkeeping.
		const decoratedNodes = flowNodes.map((n) => ({ ...n, selected: false, dragging: false }));
		const decoratedEdges = flowEdges.map((e) => ({ ...e, selected: false }));

		expect(fromFlowGraph(decoratedNodes, decoratedEdges)).toEqual(graph);
	});
});

// ─── Graph ancestry ────────────────────────────────────────────────────────

const edge = (source: string, target: string): FlowEdge =>
	({ id: `${source}->${target}`, source, target }) as FlowEdge;

describe('getAncestorNodeIds', () => {
	it('walks a linear chain back to the root, nearest first', () => {
		const edges = [edge('t1', 'a1'), edge('a1', 'a2'), edge('a2', 'a3')];
		expect(getAncestorNodeIds(edges, 'a3')).toEqual(['a2', 'a1', 't1']);
	});

	it('returns no ancestors for a root node', () => {
		expect(getAncestorNodeIds([edge('t1', 'a1')], 't1')).toEqual([]);
	});

	it('collects every branch that merges into the node', () => {
		//   t1 -> b1 \
		//   t1 -> b2 -> merge
		const edges = [edge('t1', 'b1'), edge('t1', 'b2'), edge('b1', 'merge'), edge('b2', 'merge')];
		expect(getAncestorNodeIds(edges, 'merge').sort()).toEqual(['b1', 'b2', 't1']);
	});

	it('excludes nodes on sibling branches that cannot reach it', () => {
		const edges = [edge('t1', 'left'), edge('t1', 'right')];
		expect(getAncestorNodeIds(edges, 'left')).toEqual(['t1']);
	});

	it('terminates on a cycle and never reports the node as its own ancestor', () => {
		const edges = [edge('a', 'b'), edge('b', 'c'), edge('c', 'a')];
		const ancestors = getAncestorNodeIds(edges, 'a');
		expect(ancestors).not.toContain('a');
		expect(ancestors.sort()).toEqual(['b', 'c']);
	});

	it('returns nothing for a node with no incoming edges', () => {
		expect(getAncestorNodeIds([edge('x', 'y')], 'orphan')).toEqual([]);
	});
});

// ─── Variable scope ────────────────────────────────────────────────────────

const flowNode = (id: string, nodeType: string, kind: 'trigger' | 'condition' | 'action'): FlowNode =>
	({ id, type: kind, position: { x: 0, y: 0 }, data: { nodeType, kind, config: {} } }) as FlowNode;

const def = (key: string, kind: 'trigger' | 'condition' | 'action', partial: Partial<NodeTypeDef> = {}): NodeTypeDef =>
	({ key, kind, title: key, config_schema: { properties: {} }, output_ports: ['out'], ...partial }) as NodeTypeDef;

describe('buildVariableScope', () => {
	const defs = [
		def('trigger.filesystem', 'trigger', {
			title: 'File Watcher',
			outputs: [
				{ key: 'path', type: 'string' },
				{ key: 'ext', type: 'string' }
			]
		}),
		def('trigger.manual', 'trigger', { title: 'Manual', dynamic_outputs: true }),
		def('action.index_model', 'action', {
			title: 'Index Model',
			outputs: [{ key: 'model_id', type: 'string' }]
		}),
		def('action.add_tag', 'action', { title: 'Add Tag', outputs: [{ key: 'tag_id', type: 'string' }] })
	];

	const nodes = [
		flowNode('fs_1', 'trigger.filesystem', 'trigger'),
		flowNode('idx_1', 'action.index_model', 'action'),
		flowNode('tag_1', 'action.add_tag', 'action')
	];
	const edges = [edge('fs_1', 'idx_1'), edge('idx_1', 'tag_1')];

	it("exposes a trigger ancestor's outputs as event.*", () => {
		const scope = buildVariableScope(nodes, edges, 'idx_1', defs);
		expect(scope.event.map((v) => v.path)).toEqual(['event.path', 'event.ext']);
		expect(scope.eventDynamic).toBe(false);
	});

	it('exposes non-trigger ancestors as upstream.<node_id>.*', () => {
		const scope = buildVariableScope(nodes, edges, 'tag_1', defs);
		expect(scope.upstream).toHaveLength(1);
		expect(scope.upstream[0].nodeId).toBe('idx_1');
		expect(scope.upstream[0].title).toBe('Index Model');
		expect(scope.upstream[0].outputs.map((v) => v.path)).toEqual(['upstream.idx_1.model_id']);
	});

	it('never offers the node its own outputs', () => {
		const scope = buildVariableScope(nodes, edges, 'idx_1', defs);
		expect(scope.upstream).toHaveLength(0);
	});

	it('flags a dynamic trigger payload rather than reporting no fields', () => {
		const graph = [flowNode('m_1', 'trigger.manual', 'trigger'), flowNode('a_1', 'action.add_tag', 'action')];
		const scope = buildVariableScope(graph, [edge('m_1', 'a_1')], 'a_1', defs);
		expect(scope.event).toEqual([]);
		expect(scope.eventDynamic).toBe(true);
	});

	it('unions the fields of several trigger ancestors without duplicating keys', () => {
		const otherTrigger = def('trigger.other', 'trigger', {
			outputs: [
				{ key: 'path', type: 'string' },
				{ key: 'extra', type: 'string' }
			]
		});
		const graph = [
			flowNode('fs_1', 'trigger.filesystem', 'trigger'),
			flowNode('o_1', 'trigger.other', 'trigger'),
			flowNode('a_1', 'action.add_tag', 'action')
		];
		const scope = buildVariableScope(graph, [edge('fs_1', 'a_1'), edge('o_1', 'a_1')], 'a_1', [
			...defs,
			otherTrigger
		]);
		expect(scope.event.map((v) => v.key)).toEqual(['path', 'ext', 'extra']);
	});

	it('ignores ancestors whose node type is not in the catalog', () => {
		const graph = [flowNode('ghost', 'action.unknown', 'action'), flowNode('a_1', 'action.add_tag', 'action')];
		const scope = buildVariableScope(graph, [edge('ghost', 'a_1')], 'a_1', defs);
		expect(scope.upstream).toEqual([]);
	});
});

// ─── Undo / redo ───────────────────────────────────────────────────────────

const snap = (ids: string[]): GraphSnapshot => ({
	nodes: ids.map((id) => flowNode(id, 'action.add_tag', 'action')),
	edges: []
});

describe('pushHistory', () => {
	it('records the present as the state undo returns to', () => {
		const history = pushHistory(emptyHistory(), snap(['a']));
		expect(history.past).toHaveLength(1);
		expect(history.past[0].nodes.map((n) => n.id)).toEqual(['a']);
	});

	it('drops the redo branch on a new edit', () => {
		let history: History = { past: [snap(['a'])], future: [snap(['z'])], lastKey: null };
		history = pushHistory(history, snap(['b']));
		expect(history.future).toEqual([]);
	});

	it('skips a no-op edit that leaves the graph unchanged', () => {
		const history = pushHistory(pushHistory(emptyHistory(), snap(['a'])), snap(['a']));
		expect(history.past).toHaveLength(1);
	});

	it('coalesces a run of edits sharing a key into one undo step', () => {
		// Typing "abc" into one config field must not create three undo entries.
		let history = pushHistory(emptyHistory(), snap(['a']), { coalesceKey: 'config:n1' });
		history = pushHistory(history, snap(['a', 'b']), { coalesceKey: 'config:n1' });
		history = pushHistory(history, snap(['a', 'b', 'c']), { coalesceKey: 'config:n1' });

		expect(history.past).toHaveLength(1);
		// The one entry is the state from *before* the run began.
		expect(history.past[0].nodes.map((n) => n.id)).toEqual(['a']);
	});

	it('starts a new undo step when the coalesce key changes', () => {
		let history = pushHistory(emptyHistory(), snap(['a']), { coalesceKey: 'config:n1' });
		history = pushHistory(history, snap(['a', 'b']), { coalesceKey: 'config:n2' });
		expect(history.past).toHaveLength(2);
	});

	it('an uncoalesced edit after a coalesced run is recorded separately', () => {
		let history = pushHistory(emptyHistory(), snap(['a']), { coalesceKey: 'config:n1' });
		history = pushHistory(history, snap(['a', 'b']));
		expect(history.past).toHaveLength(2);
	});

	it('bounds the stack, discarding the oldest entries', () => {
		let history = emptyHistory();
		for (let i = 0; i < 8; i++) history = pushHistory(history, snap([`n${i}`]), { cap: 3 });
		expect(history.past).toHaveLength(3);
		expect(history.past[0].nodes[0].id).toBe('n5');
	});

	it('snapshots defensively so later mutation of the live array cannot corrupt it', () => {
		const present = snap(['a']);
		const history = pushHistory(emptyHistory(), present);
		present.nodes.push(flowNode('b', 'action.add_tag', 'action'));
		expect(history.past[0].nodes).toHaveLength(1);
	});
});

describe('undoHistory / redoHistory', () => {
	it('returns null when there is nothing to undo or redo', () => {
		expect(undoHistory(emptyHistory(), snap(['a']))).toBeNull();
		expect(redoHistory(emptyHistory(), snap(['a']))).toBeNull();
	});

	it('undo restores the previous graph and banks the present for redo', () => {
		const history = pushHistory(emptyHistory(), snap(['a']));
		const result = undoHistory(history, snap(['a', 'b']))!;

		expect(result.present.nodes.map((n) => n.id)).toEqual(['a']);
		expect(result.history.past).toEqual([]);
		expect(result.history.future[0].nodes.map((n) => n.id)).toEqual(['a', 'b']);
	});

	it('round-trips undo then redo back to the starting graph', () => {
		const history = pushHistory(emptyHistory(), snap(['a']));
		const undone = undoHistory(history, snap(['a', 'b']))!;
		const redone = redoHistory(undone.history, undone.present)!;

		expect(redone.present.nodes.map((n) => n.id)).toEqual(['a', 'b']);
		expect(redone.history.past[0].nodes.map((n) => n.id)).toEqual(['a']);
	});

	it('clears the coalesce key so an edit after undo starts a fresh step', () => {
		const history = pushHistory(emptyHistory(), snap(['a']), { coalesceKey: 'config:n1' });
		const undone = undoHistory(history, snap(['a', 'b']))!;
		expect(undone.history.lastKey).toBeNull();
	});

	it('supports repeated undo down the stack', () => {
		let history = pushHistory(emptyHistory(), snap(['a']));
		history = pushHistory(history, snap(['a', 'b']));

		const first = undoHistory(history, snap(['a', 'b', 'c']))!;
		expect(first.present.nodes.map((n) => n.id)).toEqual(['a', 'b']);

		const second = undoHistory(first.history, first.present)!;
		expect(second.present.nodes.map((n) => n.id)).toEqual(['a']);
		expect(undoHistory(second.history, second.present)).toBeNull();
	});
});
