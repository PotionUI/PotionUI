import { describe, expect, it } from 'vitest';
import { layoutGraph, type LayoutEdge, type LayoutNode } from './autoLayout';

function positionsToObject(positions: Map<string, { x: number; y: number }>) {
	return Object.fromEntries(positions);
}

describe('layoutGraph', () => {
	it('returns an empty map for an empty graph', () => {
		expect(layoutGraph([], [])).toEqual(new Map());
	});

	it('places a single node at the origin', () => {
		const positions = layoutGraph([{ id: 'a' }], []);
		expect(positions.get('a')).toEqual({ x: 0, y: 0 });
	});

	it('lays out a linear chain with strictly increasing x and identical y', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }, { id: 'C' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'B', target: 'C' }
		];

		const positions = layoutGraph(nodes, edges);
		const a = positions.get('A')!;
		const b = positions.get('B')!;
		const c = positions.get('C')!;

		expect(a.x).toBeLessThan(b.x);
		expect(b.x).toBeLessThan(c.x);
		expect(a.y).toBe(b.y);
		expect(b.y).toBe(c.y);
	});

	it('places diamond siblings in the same layer with distinct y, and the join one layer past both', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'A', target: 'C' },
			{ source: 'B', target: 'D' },
			{ source: 'C', target: 'D' }
		];

		const positions = layoutGraph(nodes, edges);
		const a = positions.get('A')!;
		const b = positions.get('B')!;
		const c = positions.get('C')!;
		const d = positions.get('D')!;

		// B and C share a layer (same x) but sit at distinct y.
		expect(b.x).toBe(c.x);
		expect(b.y).not.toBe(c.y);

		// D is exactly one layer past B/C, which is itself one layer past A.
		expect(b.x).toBeGreaterThan(a.x);
		expect(d.x).toBeGreaterThan(b.x);
		expect(d.x - b.x).toBe(b.x - a.x);
	});

	it('places both targets of a condition-style fan-out in the same layer', () => {
		const nodes: LayoutNode[] = [{ id: 'cond' }, { id: 'true_branch' }, { id: 'false_branch' }];
		const edges: LayoutEdge[] = [
			{ source: 'cond', target: 'true_branch' },
			{ source: 'cond', target: 'false_branch' }
		];

		const positions = layoutGraph(nodes, edges);
		const t = positions.get('true_branch')!;
		const f = positions.get('false_branch')!;

		expect(t.x).toBe(f.x);
		expect(t.y).not.toBe(f.y);
	});

	it('terminates and places both nodes for a 2-cycle (A -> B -> A)', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'B', target: 'A' }
		];

		const positions = layoutGraph(nodes, edges);

		expect(positions.size).toBe(2);
		expect(positions.has('A')).toBe(true);
		expect(positions.has('B')).toBe(true);
		// Distinct positions - the fallback still lays them out rather than stacking them.
		expect(positions.get('A')).not.toEqual(positions.get('B'));
	});

	it('is deterministic across repeated calls', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'A', target: 'C' },
			{ source: 'B', target: 'D' },
			{ source: 'C', target: 'D' }
		];

		const first = positionsToObject(layoutGraph(nodes, edges));
		const second = positionsToObject(layoutGraph(nodes, edges));
		expect(second).toEqual(first);
	});

	it('is deterministic regardless of input node array order (tie-break by id, not insertion order)', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }];
		const shuffledNodes: LayoutNode[] = [{ id: 'D' }, { id: 'B' }, { id: 'A' }, { id: 'C' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'A', target: 'C' },
			{ source: 'B', target: 'D' },
			{ source: 'C', target: 'D' }
		];

		const ordered = positionsToObject(layoutGraph(nodes, edges));
		const shuffled = positionsToObject(layoutGraph(shuffledNodes, edges));
		expect(shuffled).toEqual(ordered);
	});

	it('places all nodes of disconnected components without overlapping at the same layer+order', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }, { id: 'X' }, { id: 'Y' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'X', target: 'Y' }
		];

		const positions = layoutGraph(nodes, edges);
		expect(positions.size).toBe(4);

		// A and X are both roots (layer 0) - they must not sit on top of each other.
		expect(positions.get('A')).not.toEqual(positions.get('X'));
		// B and Y are both at layer 1 - same constraint.
		expect(positions.get('B')).not.toEqual(positions.get('Y'));
	});

	it('ignores edges that reference a nonexistent node instead of crashing', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }];
		const edges: LayoutEdge[] = [
			{ source: 'A', target: 'B' },
			{ source: 'A', target: 'ghost' },
			{ source: 'phantom', target: 'B' }
		];

		const positions = layoutGraph(nodes, edges);
		expect(positions.size).toBe(2);
		expect(positions.get('A')!.x).toBeLessThan(positions.get('B')!.x);
	});

	it('honours custom spacing options', () => {
		const nodes: LayoutNode[] = [{ id: 'A' }, { id: 'B' }];
		const edges: LayoutEdge[] = [{ source: 'A', target: 'B' }];

		const defaultPositions = layoutGraph(nodes, edges);
		const customPositions = layoutGraph(nodes, edges, {
			nodeWidth: 100,
			nodeHeight: 50,
			hGap: 20,
			vGap: 10
		});

		const defaultDeltaX = defaultPositions.get('B')!.x - defaultPositions.get('A')!.x;
		const customDeltaX = customPositions.get('B')!.x - customPositions.get('A')!.x;

		expect(defaultDeltaX).toBe(260 + 90);
		expect(customDeltaX).toBe(100 + 20);
	});
});
