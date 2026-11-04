import { tileKey } from './tiles';
import type { ElixirNode, Run } from './types';

export const FILE_PICKS = [
	{ id: 'all', label: 'All files' },
	{ id: 'random', label: 'Random' }
];

type FilesNode = Pick<ElixirNode, 'assets' | 'pick' | 'pick_count'>;

export function drawnCount(node: FilesNode | null | undefined): number {
	const total = node?.assets?.length ?? 0;
	if (node?.pick !== 'random') return total;
	const count = Math.floor(Number(node.pick_count ?? 1));
	return Math.min(Math.max(Number.isFinite(count) ? count : 1, 1), total);
}

export function pickLine(node: FilesNode | null | undefined): string | null {
	const total = node?.assets?.length ?? 0;
	if (node?.pick !== 'random' || total === 0) return null;
	return `Random ${drawnCount(node)} of ${total}`;
}

export function drawnKeys(run: Run | null | undefined, nodeId: string): Set<string> | null {
	const state = run?.node_states?.[nodeId];
	if (!state?.draw) return null;
	return new Set(Object.values(state.manifest ?? {}).flat().map(tileKey));
}
