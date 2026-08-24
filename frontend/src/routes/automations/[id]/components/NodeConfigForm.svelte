<script lang="ts">
	/**
	 * Schema-driven node config form. Reuses `FormField.svelte` (the field-type
	 * registry render loop) directly instead of `DynamicForm.svelte` — the
	 * catalog's `config_schema` is a FLAT `{properties: {field_name: field_config}}`
	 * map (no nested root/children tree the way preset `form.yml` schemas are),
	 * so each entry is rendered as a leaf field, matching how `GroupField.svelte`
	 * renders its own leaf children (`value={value?.[fieldName]}` per field).
	 *
	 * Conditional visibility reuses the reaction engine's `processAllFieldReactions`
	 * (the array-of-fields entry point in `$lib/form/reactions.ts`) rather than
	 * `processSchemaWithReactions` (which expects the nested root/children shape) —
	 * functionally equivalent for a flat field list.
	 */
	import FormField from '$lib/components/form-fields/FormField.svelte';
	import VariablePicker, { type InsertMode } from '$lib/automations/VariablePicker.svelte';
	import { processAllFieldReactions, type FieldConfig } from '$lib/form/reactions';
	import { getDefaultForField } from '$lib/form/defaults';
	import type { ConfigSchema } from '$lib/types/automations';
	import type { VariableScope } from '$lib/stores/automationEditor';

	let {
		schema,
		value = {},
		onChange,
		scope
	}: {
		schema: ConfigSchema | null | undefined;
		value?: Record<string, any>;
		onChange: (value: Record<string, any>) => void;
		/** What this node may reference, from its ancestors' declared outputs.
		 *  Absent for a trigger (nothing runs before it). */
		scope?: VariableScope;
	} = $props();

	/** The reaction engine's `FieldConfig` is shared with preset forms; these two
	 *  markers are automation-only, so they're layered on here rather than added
	 *  to that type. Both ride along on the catalog's field defs at runtime. */
	type AutomationFieldConfig = FieldConfig & {
		templatable?: boolean;
		input_ref?: 'path' | 'expression';
	};

	/**
	 * Which insertion syntax a field takes, or `null` if it accepts no references.
	 * Actions interpolate Jinja; conditions take a bare dot-path. See
	 * `VariablePicker`'s header comment.
	 */
	function insertModeFor(field: AutomationFieldConfig): InsertMode | null {
		if (field.templatable) return 'template';
		if (field.input_ref === 'path') return 'path';
		if (field.input_ref === 'expression') return 'expression';
		return null;
	}

	function insertInto(fieldName: string, text: string, replace: boolean) {
		const current = replace ? '' : (value?.[fieldName] ?? '');
		const prefix = typeof current === 'string' ? current : '';
		handleFieldChange(fieldName, `${prefix}${text}`);
	}

	function buildFields(s: ConfigSchema | null | undefined): AutomationFieldConfig[] {
		if (!s?.properties) return [];
		return Object.entries(s.properties).map(([entryName, config]) => ({
			...config,
			name: config.name || entryName
		}));
	}

	let fields = $derived(buildFields(schema));

	// Seed defaults for any keys missing from `value` whenever the node's
	// schema (or the value coming from a freshly-selected node) changes.
	$effect(() => {
		const current = value ?? {};
		let patch: Record<string, any> | null = null;
		for (const field of fields) {
			if (field.name && !(field.name in current)) {
				const defaultValue = getDefaultForField(field);
				if (defaultValue !== undefined) {
					patch ??= {};
					patch[field.name] = defaultValue;
				}
			}
		}
		if (patch) onChange({ ...current, ...patch });
	});

	let processedFields = $derived.by((): AutomationFieldConfig[] => {
		const valueChanges: Record<string, any> = {};
		const processed = processAllFieldReactions(fields, value ?? {}, valueChanges) as AutomationFieldConfig[];
		if (Object.keys(valueChanges).length > 0) {
			// Reaction `set_value` actions - apply outside the derivation.
			queueMicrotask(() => onChange({ ...(value ?? {}), ...valueChanges }));
		}
		return processed;
	});

	function handleFieldChange(fieldName: string, fieldValue: any) {
		onChange({ ...(value ?? {}), [fieldName]: fieldValue });
	}
</script>

{#if processedFields.length === 0}
	<p class="text-xs text-fg-subtle">This node has no configuration.</p>
{:else}
	<div class="space-y-3">
		{#each processedFields as field (field.name)}
			{@const mode = insertModeFor(field)}
			<div>
				{#if mode && scope && field.name}
					<div class="flex justify-end -mb-1">
						<VariablePicker
							{scope}
							{mode}
							onInsert={(text, replace) => insertInto(field.name!, text, replace)}
						/>
					</div>
				{/if}
				<FormField
					name={field.name ?? null}
					config={field}
					value={value?.[field.name ?? '']}
					onChange={handleFieldChange}
				/>
			</div>
		{/each}
	</div>
{/if}
