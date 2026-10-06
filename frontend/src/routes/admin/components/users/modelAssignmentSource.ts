import { api } from '$lib/services/api/index';
import type { RemoteQuery, RemoteSource } from '$lib/components/picker';
import type { PickerModel } from '$lib/components/picker/kinds';

export interface ModelAssignmentScope {
	userId?: string;
	groupId?: string;
}

const ASSIGNED_PAGE_SIZE = 200;

function scopeParams(scope: ModelAssignmentScope, filter: 'assigned' | 'unassigned') {
	return {
		assignment_filter: filter,
		assigned_user_id: scope.userId,
		assigned_group_id: scope.groupId
	};
}

export async function fetchAssignedModels(scope: ModelAssignmentScope): Promise<PickerModel[]> {
	const rows: PickerModel[] = [];
	let offset = 0;
	for (;;) {
		const response = await api.getModels({
			all_models: true,
			include_tags: true,
			limit: ASSIGNED_PAGE_SIZE,
			offset,
			...scopeParams(scope, 'assigned')
		});
		if (!response.success || !response.data) throw new Error(response.message || 'Failed to load assigned models');
		const page = (response.data.models || []) as PickerModel[];
		rows.push(...page);
		offset += page.length;
		if (page.length === 0 || offset >= (response.data.total || 0)) break;
	}
	return rows;
}

export async function fetchModelTypeOptions(): Promise<{ value: string; label: string }[]> {
	try {
		const response = await api.getModelTypes({});
		if (!response.success || !response.data) return [];
		return (response.data.types || []).map((t: { type: string }) => ({ value: t.type, label: t.type }));
	} catch {
		return [];
	}
}

export function createModelRemote(
	scope: ModelAssignmentScope,
	typeOptions: { value: string; label: string }[]
): RemoteSource<PickerModel> {
	return {
		pageSize: 50,
		filterOptions: { type: typeOptions },
		fetch: async (query: RemoteQuery) => {
			const response = await api.getModels({
				all_models: true,
				include_tags: true,
				limit: query.limit,
				offset: query.offset,
				search: query.q || undefined,
				model_type: query.filters.type || undefined,
				...(query.view === 'all' ? {} : scopeParams(scope, query.view))
			});
			if (!response.success || !response.data) throw new Error(response.message || 'Failed to load models');
			return { rows: (response.data.models || []) as PickerModel[], total: response.data.total || 0 };
		}
	};
}
