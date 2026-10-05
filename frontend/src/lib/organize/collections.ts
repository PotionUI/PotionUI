import { api } from '$lib/services/api';
import { buildTree, flattenTree } from '$lib/components/pane';
import type { OrganizeCollectionScope } from '$lib/types/organize';

export interface PlainCollection {
	id: string;
	name: string;
	parent_id: string | null;
	item_count: number;
}

export async function loadCollections(scope: OrganizeCollectionScope): Promise<PlainCollection[]> {
	const response =
		scope === 'models' ? await api.listModelCollections() : await api.listCollections(scope);
	const list = response.success ? (response.data?.collections ?? []) : [];
	return list.map((c: PlainCollection) => ({
		id: c.id,
		name: c.name,
		parent_id: c.parent_id ?? null,
		item_count: c.item_count ?? 0
	}));
}

export function collectionOptions(collections: PlainCollection[]): { value: string; label: string }[] {
	return flattenTree(buildTree(collections)).map((node) => ({
		value: node.item.id,
		label: `${'  '.repeat(node.depth)}${node.item.name}`
	}));
}
