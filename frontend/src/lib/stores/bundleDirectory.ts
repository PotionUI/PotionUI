import { writable } from 'svelte/store';
import { api } from '$lib/services/api';
import { modelDisplayName } from '$lib/utils/modelDisplay';
import { MODEL_REF_PREFIX, bundleItemId, type BundleDirectories, type BundleInfo, type BundleRef } from '$lib/utils/bundleResources';

export const bundleDirectories = writable<BundleDirectories>({});

const requested = new Set<string>();

function remember(source: string, itemKey: string, patch: Partial<BundleInfo>): void {
	bundleDirectories.update((current) => {
		const directory = current[source] ?? {};
		return {
			...current,
			[source]: { ...directory, [itemKey]: { ...(directory[itemKey] ?? { name: null, members: null }), ...patch } }
		};
	});
}

async function loadName(source: string, itemKey: string): Promise<void> {
	if (!itemKey.startsWith(MODEL_REF_PREFIX)) return;
	try {
		const response = await api.getModelById(bundleItemId(itemKey));
		const name = response.success ? modelDisplayName(response.data?.model) : '';
		if (name) remember(source, itemKey, { name });
	} catch {
		return;
	}
}

async function loadMembers(source: string, itemKey: string): Promise<void> {
	try {
		const response = await api.getPromptResourceMembers(source, itemKey);
		const members = response.success ? response.data?.members : undefined;
		remember(source, itemKey, { members: Array.isArray(members) ? members : [] });
	} catch {
		remember(source, itemKey, { members: [] });
	}
}

export function loadBundles(refs: readonly BundleRef[]): void {
	for (const { source, itemKey } of refs) {
		const key = `${source}\u0000${itemKey}`;
		if (!source || !itemKey || requested.has(key)) continue;
		requested.add(key);
		void loadName(source, itemKey);
		void loadMembers(source, itemKey);
	}
}

export function resetBundleDirectories(): void {
	requested.clear();
	bundleDirectories.set({});
}
