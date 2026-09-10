// A subdirectory list arrives depot-relative and pre-sorted (parents before
// children, e.g. "sdxl", "sdxl/characters"). This derives display metadata
// (indent depth, leaf name, muted parent breadcrumb) without re-sorting.
export interface SubdirNode {
	path: string;
	depth: number;
	leaf: string;
	parentLabel: string;
}

export function buildSubdirNodes(subdirectories: string[]): SubdirNode[] {
	return subdirectories.map((path) => {
		const segments = path.split('/').filter(Boolean);
		return {
			path,
			depth: Math.max(segments.length - 1, 0),
			leaf: segments[segments.length - 1] ?? path,
			parentLabel: segments.slice(0, -1).join(' / ')
		};
	});
}
