// Shared contract between CollectionLibrarySidebar and the domain adapters
// (history/models) that own the actual store calls.

import type { TreeItem } from '$lib/components/pane';

// What the sidebar needs beyond the bare tree shape (name for display/sort,
// item_count for the per-row badge). Both Collection (history) and
// ModelCollection (models) satisfy this structurally without importing
// either domain type here.
export interface CollectionLike extends TreeItem {
	name: string;
	item_count: number;
}

export interface MutationResult {
	success: boolean;
	error?: string;
	message?: string;
}

export interface BulkMoveResult {
	success: boolean;
	error?: string;
	message?: string;
	moved: number;
	failed: number;
	errors: { id: string; reason: string }[];
}

export interface SmartView {
	id: string;
	icon: string;
	label: string;
	active: boolean;
	onSelect: () => void | Promise<void>;
}

export interface TreeActions {
	onSelect: (id: string) => void | Promise<void>;
	onRename: (id: string, name: string) => Promise<MutationResult>;
	onCreate: (name: string, parentId: string) => Promise<MutationResult>;
	// blockedIds = the deleted node's id plus every descendant id, so the
	// adapter can tell whether its own active selection just went away.
	onDelete: (id: string, blockedIds: Set<string>) => Promise<MutationResult>;
	onMove: (id: string, parentId: string | null) => Promise<MutationResult>;
	// Reparent several collections in one call. Per-id failures (a cycle, an
	// ownership mismatch) don't block the rest of the batch - see BulkMoveResult.
	onBulkMove: (ids: string[], parentId: string | null) => Promise<BulkMoveResult>;
}
