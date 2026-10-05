export interface CollectionSelection {
	collectionId?: string;
	unsorted?: boolean;
	directOnly?: boolean;
}

export interface CollectionQueryParams {
	collection_id?: string;
	include_descendants?: boolean;
	unsorted?: boolean;
}

export function collectionQueryParams(selection: CollectionSelection): CollectionQueryParams {
	if (selection.unsorted) return { unsorted: true };
	if (!selection.collectionId) return {};
	const params: CollectionQueryParams = { collection_id: selection.collectionId };
	if (selection.directOnly) params.include_descendants = false;
	return params;
}

export function collectionQueryStrings(selection: CollectionSelection): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [key, value] of Object.entries(collectionQueryParams(selection))) {
		out[key] = String(value);
	}
	return out;
}

export function collectionListParams(directOnly: boolean): { include_descendants?: boolean } {
	return directOnly ? { include_descendants: false } : {};
}
