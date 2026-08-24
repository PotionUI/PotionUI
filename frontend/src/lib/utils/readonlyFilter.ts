/**
 * Folds an admin-locked field (per-field form overrides) into the same
 * `disabled` flag the reaction engine's `set_disabled` action already renders
 * through every leaf field component (see `$lib/form/reactions.ts` and e.g.
 * `TextInput.svelte`'s `config.disabled`). `GET /api/presets/{id}/form` marks a
 * locked field with `readonly: true`; this reuses the existing render path
 * instead of adding a second disabled mechanism to every field component.
 *
 * Must run AFTER reaction processing so a reaction can never re-enable a field
 * the admin locked.
 */

interface ReadonlyNode {
	readonly?: boolean;
	disabled?: boolean;
	children?: ReadonlyNode[];
	[key: string]: unknown;
}

/** Mutates `node` (and its descendants) in place: `disabled` becomes `true`
 *  wherever `readonly` is set, without ever clearing a `disabled` a reaction
 *  already set. */
export function applyFieldReadonly<T extends ReadonlyNode>(node: T): void {
	if (!node) return;
	if (node.readonly) node.disabled = true;
	if (Array.isArray(node.children)) {
		for (const child of node.children) applyFieldReadonly(child);
	}
}

/** Applies `applyFieldReadonly` to every top-level field of a
 *  `GET /api/presets/{id}/form` schema (`{properties: {rootKey: {children: [...]}}}`),
 *  mutating and returning the same schema object. Safe to call on a schema
 *  that's already been deep-cloned for reaction processing. */
export function applyReadonlyToSchema(
	schema: { properties?: Record<string, { children?: ReadonlyNode[] }> } | null | undefined
): typeof schema {
	if (!schema || !schema.properties) return schema;
	for (const rootKey of Object.keys(schema.properties)) {
		const children = schema.properties[rootKey]?.children;
		if (Array.isArray(children)) {
			for (const child of children) applyFieldReadonly(child);
		}
	}
	return schema;
}
