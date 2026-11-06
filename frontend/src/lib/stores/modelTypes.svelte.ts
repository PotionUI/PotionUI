import { toStore } from 'svelte/store';
import type { ModelTypeDefinition } from '$lib/types/models';

const catalog = $state<{ types: ModelTypeDefinition[]; loaded: boolean }>({ types: [], loaded: false });

export function setModelTypes(types: readonly ModelTypeDefinition[]): void {
	catalog.types = [...types];
	catalog.loaded = true;
}

export function resetModelTypes(): void {
	catalog.types = [];
	catalog.loaded = false;
}

export function modelTypeDefinitions(): readonly ModelTypeDefinition[] {
	return catalog.types;
}

export function modelTypesLoaded(): boolean {
	return catalog.loaded;
}

export function findModelType(id: string | null | undefined): ModelTypeDefinition | undefined {
	if (!id) return undefined;
	return catalog.types.find((definition) => definition.id === id);
}

export function assignableModelTypes(): string[] {
	return catalog.types.filter((definition) => definition.assignable && !definition.virtual).map((definition) => definition.id);
}

export function depotModelTypes(): string[] {
	return catalog.types.filter((definition) => !definition.virtual).map((definition) => definition.id);
}

export const modelTypes = toStore(() => catalog.types);
