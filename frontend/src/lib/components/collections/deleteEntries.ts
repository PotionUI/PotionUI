import type { ConfirmOptions } from '$lib/stores/confirm';
import type { APIResponse } from '$lib/types/api';

export type EntriesScope = 'history' | 'library';

export interface DeleteEntriesBlocked {
	id: string;
	reason: string;
}

export interface DeleteEntriesResult {
	deleted: number;
	blocked: DeleteEntriesBlocked[];
	total: number;
}

export interface CollectionEntriesActions {
	scope: EntriesScope;
	count: (collectionId: string) => Promise<number>;
	deleteAll: (collectionId: string) => Promise<DeleteEntriesResult>;
}

export interface EntriesApi {
	list: (
		scope: EntriesScope,
		directOnly: boolean
	) => Promise<APIResponse<{ collections: { id: string; item_count?: number | null }[] }>>;
	deleteEntries: (collectionId: string) => Promise<APIResponse<DeleteEntriesResult>>;
}

export function createEntriesActions(
	scope: EntriesScope,
	entriesApi: EntriesApi,
	reload: () => Promise<unknown>
): CollectionEntriesActions {
	return {
		scope,
		async count(collectionId) {
			const response = await entriesApi.list(scope, true);
			if (!response.success || !response.data) throw new Error(response.message || 'Could not count the entries.');
			const found = response.data.collections.find((collection) => collection.id === collectionId);
			if (!found) throw new Error('This collection no longer exists.');
			return found.item_count ?? 0;
		},
		async deleteAll(collectionId) {
			try {
				const response = await entriesApi.deleteEntries(collectionId);
				if (!response.success || !response.data) throw new Error(response.message || 'Failed to delete entries.');
				return response.data;
			} finally {
				await reload();
			}
		}
	};
}

export interface DeleteEntriesUi {
	confirm: (options: ConfirmOptions) => Promise<boolean>;
	success: (message: string) => void;
	info: (message: string) => void;
	error: (message: string) => void;
	failed: (error: unknown) => void;
}

export type DeleteEntriesOutcome = 'empty' | 'cancelled' | 'deleted' | 'partial' | 'failed';

function noun(scope: EntriesScope, count: number): string {
	if (scope === 'history') return count === 1 ? 'generation' : 'generations';
	return count === 1 ? 'item' : 'items';
}

export function deleteEntriesCopy(name: string, count: number, scope: EntriesScope): ConfirmOptions {
	const what = `${count} ${noun(scope, count)}`;
	const files = count === 1 ? 'its files' : 'their files';
	return {
		title: 'Delete entries?',
		message: `Delete all ${what} in “${name}”, with ${files}? ${count === 1 ? 'It is' : 'They are'} deleted everywhere, not only removed from this collection. The collection itself stays. This cannot be undone.`,
		variant: 'danger',
		confirmLabel: `Delete ${what}`
	};
}

export function deleteEntriesSummary(result: DeleteEntriesResult, scope: EntriesScope): { ok: boolean; message: string } {
	const blocked = result.blocked.length;
	if (blocked === 0) {
		return { ok: true, message: `Deleted ${result.deleted} ${noun(scope, result.deleted)}.` };
	}
	const reason = result.blocked[0].reason;
	return {
		ok: false,
		message: `Deleted ${result.deleted} of ${result.total}. ${blocked} could not be deleted: ${reason}`
	};
}

export async function runDeleteEntries(
	collection: { id: string; name: string },
	actions: CollectionEntriesActions,
	ui: DeleteEntriesUi
): Promise<DeleteEntriesOutcome> {
	let count: number;
	try {
		count = await actions.count(collection.id);
	} catch (error) {
		ui.failed(error);
		return 'failed';
	}
	if (count <= 0) {
		ui.info(`“${collection.name}” has no entries to delete.`);
		return 'empty';
	}
	const confirmed = await ui.confirm(deleteEntriesCopy(collection.name, count, actions.scope));
	if (!confirmed) return 'cancelled';
	let result: DeleteEntriesResult;
	try {
		result = await actions.deleteAll(collection.id);
	} catch (error) {
		ui.failed(error);
		return 'failed';
	}
	const summary = deleteEntriesSummary(result, actions.scope);
	if (summary.ok) {
		ui.success(summary.message);
		return 'deleted';
	}
	ui.error(summary.message);
	return 'partial';
}
