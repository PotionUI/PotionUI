export type FormAudience = 'simple' | 'advanced';

/** Minimal shape this module cares about — deliberately loose (`any`-ish)
 *  because it walks the same free-form field-tree as `src/lib/form/reactions.ts`. */
interface AudienceNode {
	name?: string;
	audience?: FormAudience;
	visible?: boolean;
	hidden_when_video_director?: boolean;
	children?: AudienceNode[];
	[key: string]: unknown;
}

/**
 * Narrows `visible` on a field tree for the given audience and Video
 * Director state, in place, and returns whether the node itself ended up
 * visible.
 *
 * - A leaf field marked `audience: 'advanced'` is hidden in `'simple'` mode.
 * - A leaf field marked `hidden_when_video_director: true` is hidden
 *   whenever `videoDirectorActive` is true — for a field whose form value is
 *   a fallback the Director's own document overrides once attached (every UI
 *   generation in a mode the Director owns), but that still has to render
 *   for a mode usable outside the Director too.
 *   Hidden means "don't render it" only — callers must not drop its value
 *   from formData, so this never touches anything but `visible`.
 * - A container (any node with a non-empty `children` array — row/tab/tabs/
 *   accordion/group all share this shape) collapses to hidden once every
 *   descendant is hidden, so an all-hidden tab disappears instead of
 *   rendering an empty shell.
 * - A node already hidden by an upstream reaction (`visible === false`)
 *   stays hidden regardless of audience or Director state.
 * - `forceVisibleNames`, when given, auto-reveals a leaf field named in the set
 *   even if it's `audience: 'advanced'` and we're in `'simple'` mode — used to
 *   surface a field that failed server-side validation (see DynamicForm's
 *   `fieldErrors` prop) without flipping the global Simple/Advanced toggle.
 */
export function applyAudienceVisibility<T extends AudienceNode>(
	node: T,
	audience: FormAudience,
	forceVisibleNames?: ReadonlySet<string>,
	videoDirectorActive = false
): boolean {
	if (!node) return false;
	if (node.visible === false) return false;

	if (Array.isArray(node.children) && node.children.length > 0) {
		let anyChildVisible = false;
		for (const child of node.children) {
			if (applyAudienceVisibility(child, audience, forceVisibleNames, videoDirectorActive)) {
				anyChildVisible = true;
			}
		}
		node.visible = anyChildVisible;
		return anyChildVisible;
	}

	const forced = !!(forceVisibleNames && node.name && forceVisibleNames.has(node.name));
	const hiddenForAudience = !forced && audience === 'simple' && node.audience === 'advanced';
	const hiddenForDirector = !forced && videoDirectorActive && node.hidden_when_video_director === true;
	const finalVisible = !hiddenForAudience && !hiddenForDirector;
	node.visible = finalVisible;
	return finalVisible;
}

/** Applies `applyAudienceVisibility` to every top-level field of a
 *  `GET /api/presets/{id}/form` schema (`{properties: {rootKey: {children: [...]}}}`),
 *  mutating and returning the same schema object. Safe to call on a schema
 *  that's already been deep-cloned for reaction processing. */
export function applyAudienceVisibilityToSchema(
	schema: { properties?: Record<string, { children?: AudienceNode[] }> } | null | undefined,
	audience: FormAudience,
	forceVisibleNames?: ReadonlySet<string>,
	videoDirectorActive = false
): typeof schema {
	if (!schema || !schema.properties) return schema;
	for (const rootKey of Object.keys(schema.properties)) {
		const children = schema.properties[rootKey]?.children;
		if (Array.isArray(children)) {
			for (const child of children) {
				applyAudienceVisibility(child, audience, forceVisibleNames, videoDirectorActive);
			}
		}
	}
	return schema;
}
