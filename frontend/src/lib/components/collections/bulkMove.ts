// Pure helpers for multi-selecting collections in CollectionLibrarySidebar and
// moving them together. No store/API dependency - the caller wires these into
// selection state and the "Move to…" target picker.

import { descendantIds } from '$lib/components/pane';
import type { CollectionLike } from './types';

// A selection that includes both a folder and one of its own descendants is
// redundant on the descendant's side: moving the ancestor already relocates
// its whole subtree, so the descendant would just be reparented under itself
// a second time (a no-op at best, a wasted request at worst). Drop any
// selected id that is a descendant of another selected id, keeping only the
// topmost ids the batch actually needs to move.
export function dropRedundantDescendants<T extends CollectionLike>(
	ids: Iterable<string>,
	collections: T[]
): string[] {
	const unique = Array.from(new Set(ids));
	return unique.filter(
		(id) => !unique.some((other) => other !== id && descendantIds(collections, other).has(id))
	);
}

// Every id a "Move to…" picker must not offer as the target for this
// selection: each selected id itself, plus every one of its descendants (a
// union across the whole selection). Moving any selected id into its own
// subtree is a cycle; offering that target at all would let the user pick a
// move that this same batch can't apply to that id.
export function blockedBulkMoveTargets<T extends CollectionLike>(
	ids: Iterable<string>,
	collections: T[]
): Set<string> {
	const blocked = new Set<string>();
	for (const id of ids) {
		for (const descendant of descendantIds(collections, id)) {
			blocked.add(descendant);
		}
	}
	return blocked;
}
