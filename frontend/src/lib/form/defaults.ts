/**
 * Schema default-value seeding.
 *
 * `getDefaultForField`/`getInitialDataFromFields` are extracted verbatim from
 * `DynamicForm.svelte`'s former inline `getInitialData()` (pure refactor —
 * identical behavior, just relocated so `NodeConfigForm.svelte` can reuse the
 * per-field leaf logic without DynamicForm's nested-tree/slugify machinery).
 *
 * `getSchemaDefaults(schema)` reproduces DynamicForm's original call site
 * exactly: pick the first root key, recurse into its `.children` tree.
 */

export interface DefaultableField {
	type?: string;
	name?: string;
	title?: string;
	default?: any;
	children?: DefaultableField[];
	[key: string]: any;
}

/** Helper to slugify text (verbatim from DynamicForm.svelte). */
function slugify(text: string | null | undefined): string {
	if (!text) return '';
	return text
		.toString()
		.toLowerCase()
		.trim()
		.replace(/\s+/g, '_')
		.replace(/[^\w-]+/g, '')
		.replace(/--+/g, '_');
}

/** Per-leaf-field default value, by field type (verbatim branch logic from
 *  DynamicForm's `getInitialData`, minus the "only assign if defined" guard
 *  that only applied to the generic fallback branch there). Reused directly
 *  by `NodeConfigForm` over its flat `{properties}` map, and internally by
 *  `getInitialDataFromFields` below for the nested-tree walk. Returns
 *  `{included: false}` for the one case (no matching branch, no `default`)
 *  where DynamicForm's original code left the key unset entirely. */
function getFieldDefaultResult(
	field: DefaultableField
): { included: true; value: any } | { included: false } {
	if (field.type === 'checkbox' || field.type === 'boolean' || field.type === 'gate') {
		return { included: true, value: field.default !== undefined ? field.default : false };
	} else if (field.type === 'checkbox_group') {
		return { included: true, value: field.default !== undefined ? field.default : [] };
	} else if (field.type === 'select') {
		return { included: true, value: field.default !== undefined ? field.default : undefined };
	} else if (field.type === 'model') {
		return {
			included: true,
			value: {
				modelPath: field.default || '',
				tagFilters: field.configuration?.tags || []
			}
		};
	} else if (field.type === 'lora_picker') {
		return { included: true, value: field.default ?? [] };
	} else if (field.default !== undefined) {
		return { included: true, value: field.default };
	}
	return { included: false };
}

/** Convenience wrapper over `getFieldDefaultResult` for callers (like
 *  `NodeConfigForm`) that just want a value, `undefined` if there is none. */
export function getDefaultForField(field: DefaultableField): any {
	const result = getFieldDefaultResult(field);
	return result.included ? result.value : undefined;
}

/** Recursively walks a `children` field-tree (rows/tabs/accordions/groups
 *  merge their children's data; alert/markdown are display-only and skipped)
 *  and returns the flat `{fieldName: defaultValue}` map. Verbatim from
 *  DynamicForm.svelte's `getInitialData`. */
function getInitialDataFromFields(children: DefaultableField[]): Record<string, any> {
	const data: Record<string, any> = {};
	if (!Array.isArray(children)) return data;

	children.forEach((field) => {
		if (
			(field.type === 'row' ||
				field.type === 'tab' ||
				field.type === 'tabs' ||
				field.type === 'accordion' ||
				field.type === 'group' ||
				field.type === 'section') &&
			field.children
		) {
			Object.assign(data, getInitialDataFromFields(field.children));
		} else if (field.type === 'alert' || field.type === 'markdown') {
			// Skip display-only fields
			return;
		} else if (field.type === 'gate') {
			// A gate is both a value field (its own boolean) and a container -
			// register its own default, then recurse into its children the same
			// way row/tabs/accordion/group/section do.
			if (field.name) {
				const result = getFieldDefaultResult(field);
				if (result.included) {
					data[field.name] = result.value;
				}
			}
			if (field.children) {
				Object.assign(data, getInitialDataFromFields(field.children));
			}
		} else {
			const name = field.name || (field.title ? slugify(field.title) : null);
			if (name && field.name) {
				// Only create form state if field has an explicit name from backend
				const result = getFieldDefaultResult(field);
				if (result.included) {
					data[name] = result.value;
				}
			}
		}
	});
	return data;
}

/**
 * DynamicForm's original default-seeding entry point: `schema.properties` is
 * `{rootKey: {children: [...]}}` (a single root wrapping a field tree, as
 * returned by `GET /api/presets/{id}/form`). Picks the first root key and
 * recurses into its children — identical to the pre-refactor inline logic.
 */
export function getSchemaDefaults(schema: { properties?: Record<string, DefaultableField> } | null | undefined): Record<string, any> {
	if (!schema || !schema.properties) return {};
	const rootKey = Object.keys(schema.properties)[0];
	if (!rootKey) return {};
	const rootProperties = schema.properties[rootKey];
	return getInitialDataFromFields(rootProperties?.children ?? []);
}
