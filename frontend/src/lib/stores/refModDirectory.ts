import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import { modelDisplayName } from '$lib/utils/modelDisplay';
import type { RefModDirectory, RefModInfo } from '$lib/utils/refMods';

export const refModDirectory = writable<RefModDirectory>({});

const requested = new Set<string>();

function remember(id: string, patch: Partial<RefModInfo>): void {
	refModDirectory.update((current) => ({
		...current,
		[id]: { ...(current[id] ?? { name: null, members: null }), ...patch }
	}));
}

async function loadName(id: string): Promise<void> {
	try {
		const response = await api.getModelById(id);
		const name = response.success ? modelDisplayName(response.data?.model) : '';
		if (name) remember(id, { name });
	} catch {
		return;
	}
}

async function loadMembers(id: string): Promise<void> {
	try {
		const response = await api.getRefModMembers(id);
		const members = response.success ? response.data?.members : undefined;
		remember(id, { members: Array.isArray(members) ? members : [] });
	} catch {
		remember(id, { members: [] });
	}
}

export function loadRefMods(ids: readonly string[]): void {
	for (const id of ids) {
		if (!id || requested.has(id)) continue;
		requested.add(id);
		void loadName(id);
		void loadMembers(id);
	}
}

export function resetRefModDirectory(): void {
	requested.clear();
	refModDirectory.set({});
}
