import type { AttributeDefinition } from '$lib/types/models';

export function deletableAttributeIds(
	definitions: readonly AttributeDefinition[],
	selected: ReadonlySet<string>
): string[] {
	const systemIds = new Set(definitions.filter((d) => d.system).map((d) => d.id));
	return [...selected].filter((id) => !systemIds.has(id));
}

export function selectionHasBuiltIn(definitions: readonly AttributeDefinition[], selected: ReadonlySet<string>): boolean {
	const systemIds = new Set(definitions.filter((d) => d.system).map((d) => d.id));
	return [...selected].some((id) => systemIds.has(id));
}
