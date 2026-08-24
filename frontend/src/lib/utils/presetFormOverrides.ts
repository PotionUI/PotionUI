import type { PresetFormOverrideField, PresetFormOverridePatch } from '$lib/types/api';
import { extractAllFields, type FieldConfig } from '$lib/form/reactions';
import { hasFieldComponent } from '$lib/fields/registry';
import { valuesEqual } from '$lib/components/dynamicFormReactionApply';

/** The kind of editor the admin "default" override should use for a field, chosen
 *  from the field's underlying type. This is the FALLBACK editor - used only when
 *  `canUseRichEditor` can't render the field's real /generate widget (see below) -
 *  so a raw string override is still useful for simple types like seed even
 *  without a registered component or config metadata. */
export type OverrideEditorKind = 'boolean' | 'number' | 'select' | 'text';

const BOOLEAN_TYPES = new Set(['boolean', 'checkbox']);
const NUMBER_TYPES = new Set(['number', 'integer', 'slider']);
const SELECT_TYPES = new Set(['select']);

export function overrideEditorKind(field: Pick<PresetFormOverrideField, 'type' | 'options'>): OverrideEditorKind {
	if (BOOLEAN_TYPES.has(field.type)) return 'boolean';
	if (NUMBER_TYPES.has(field.type)) return 'number';
	if (SELECT_TYPES.has(field.type) && field.options && field.options.length > 0) return 'select';
	return 'text';
}

/** Local per-field edit state the admin is composing before saving. Mirrors
 *  `PresetFormOverridePatch` but always has all three keys so the UI can bind to
 *  them directly; `hasDefault` distinguishes "no default override" from a
 *  legitimately falsy override value (`0`, `false`, `''`). */
export interface PendingOverride {
	hasDefault: boolean;
	default: unknown;
	editable: boolean;
	visible: boolean;
}

/** Seeds the editable per-row state for a field from its current server-side
 *  override (or the field's own defaults when there is none yet). */
export function pendingOverrideFrom(field: PresetFormOverrideField): PendingOverride {
	const override = field.override;
	return {
		hasDefault: !!override && Object.prototype.hasOwnProperty.call(override, 'default'),
		default: override?.default ?? field.preset_default,
		editable: override?.editable ?? true,
		visible: override?.visible ?? true
	};
}

/** Turning "visible" off implies "not editable" - a field the user can't see can't
 *  be edited either. This is the single source of truth for that rule; both the
 *  row UI (to grey out/disable the editable toggle) and the payload builder (to
 *  guarantee the invariant holds server-side even if stale UI state disagrees)
 *  call it. */
export function effectiveEditable(pending: Pick<PendingOverride, 'editable' | 'visible'>): boolean {
	return pending.visible ? pending.editable : false;
}

/** True when a pending row still matches its last-saved override - i.e. nothing
 *  to send for that field. `default` is compared with `valuesEqual` (structural,
 *  not `===`) rather than a bare reference check: the rich editor (below) can
 *  set an array/object default (`checkbox_group`, `lora_picker`, …), and
 *  `processSchemaWithReactions`-style re-renders/component updates hand back a
 *  fresh array/object instance on every edit even when its contents haven't
 *  actually changed - see `valuesEqual`'s own doc comment in
 *  `dynamicFormReactionApply.ts` for why a plain `!==`/`===` is wrong there. A
 *  bare `===` here would mark those rows "dirty" forever after a single edit. */
export function isOverrideUnchanged(field: PresetFormOverrideField, pending: PendingOverride): boolean {
	const saved = pendingOverrideFrom(field);
	return (
		saved.hasDefault === pending.hasDefault &&
		valuesEqual(saved.default, pending.default) &&
		saved.editable === pending.editable &&
		saved.visible === pending.visible
	);
}

/** True when a pending row has no active override at all (equivalent to a cleared
 *  row) - editable and visible both at their implicit defaults and no default
 *  override set. */
export function isOverrideEmpty(pending: PendingOverride): boolean {
	return !pending.hasDefault && pending.editable && pending.visible;
}

/**
 * Builds the `overrides` map for `PUT /api/presets/{id}/form-overrides`: only
 * fields whose pending state actually differs from what's saved, each reduced to
 * `null` (clear) or a patch carrying just the effective values.
 */
export function buildOverridesPayload(
	fields: PresetFormOverrideField[],
	pendingByName: Record<string, PendingOverride>
): Record<string, PresetFormOverridePatch | null> {
	const payload: Record<string, PresetFormOverridePatch | null> = {};

	for (const field of fields) {
		const pending = pendingByName[field.name];
		if (!pending || isOverrideUnchanged(field, pending)) continue;

		if (isOverrideEmpty(pending)) {
			payload[field.name] = null;
			continue;
		}

		const patch: PresetFormOverridePatch = {
			editable: effectiveEditable(pending),
			visible: pending.visible
		};
		if (pending.hasDefault) patch.default = pending.default;
		payload[field.name] = patch;
	}

	return payload;
}

// ── Rich default-value editor (renders the real /generate widget) ──
//
// The override inventory (`PresetFormOverrideField`) only carries a thin
// {name, label, type, preset_default, options?} shape - not enough to drive a
// field's real component (a model field needs `model_type`/`preset_id`, a
// slider needs min/max, ...). That richer per-field config already exists in
// the preset's rendered form schema (`GET /api/presets/{id}/form`, the same
// endpoint /generate's DynamicForm uses) - `buildFieldConfigIndex` flattens it
// by field name with `$lib/form/reactions`' `extractAllFields`, the same
// tree-walk DynamicForm itself uses for `allFields`.
//
// Two caveats inherent to reusing that endpoint rather than inventing one:
//   - it returns only the mode's DEFAULT form variant, so a field declared
//     only in a non-default variant has no entry in the index here even
//     though the override inventory (a cross-variant union) still lists it;
//   - a field currently overridden `visible: false` is removed from the
//     schema entirely (`apply_overrides_to_fields`), so it also has no entry
//     while that override is active.
// Both cases - and any field type not on `RICH_EDITOR_TYPES` below - fall
// back to the plain `overrideEditorKind` editor via `canUseRichEditor`.

/** Field types whose component value shape isn't the raw wire value - mirrors
 *  `DynamicForm.svelte`'s `flattenFormData`/`mergeFormData` model-object
 *  handling (`{modelPath, tagFilters}` <-> the `model:<id>` ref string the
 *  backend stores). Every other rich-editor type is a direct passthrough
 *  (select, boolean, number, seed, checkbox_group, resolution). */
const MODEL_REF_TYPES = new Set(['model', 'models']);

/** Field types the rich editor is actually proven for: fields whose real
 *  component renders sensibly from a static admin default with no other form
 *  state around it, and whose value shape is either the raw wire value or
 *  handled by `toComponentValue`/`fromComponentValue` above. Deliberately an
 *  ALLOW-list, not a blocklist - a new/plugin field type is untested here
 *  until someone adds it, so it should fall back to the raw editor rather
 *  than silently get the rich one.
 *
 *  `lora_picker` renders its own real multi-row picker (`LoraPickerField`) -
 *  it's self-contained exactly like `model` (own model-library fetch scoped
 *  by `config.preset_id`, own search/add/remove UI) and its `onChange` value
 *  IS the wire shape already (an array of `{model, strength}` - the same
 *  shape a preset's own `default:` uses), so no `toComponentValue`/
 *  `fromComponentValue` special-casing is needed.
 *
 *  Left off, and why - these keep the raw-input fallback (with a
 *  `rawEditorHint`, see below, so the fallback is never a silent dead end):
 *   - `carousel`: potentially file/preset-scoped option loading not
 *     value-shape-verified as a single "default value" cell yet.
 *   - `prompt_timeline`, `llm`: multi-part composite editors with their own
 *     internal state machines, not single-value fields at all.
 *   - `image`, `video`, `audio`, `media`: need an interactive upload/
 *     generation-library flow (blob previews, modals) that doesn't fit a
 *     static admin default - embedding that UI in a table cell would be
 *     worse than the raw path fallback, not better.
 *   - `row`, `tab`, `tabs`, `accordion`, `group` (containers), `alert`,
 *     `markdown`, `header` (display-only): no scalar default to edit at all;
 *     vanishingly rare as NAMED override targets, but still get a hint
 *     rather than a bare, unexplained text box if one ever shows up. */
const RICH_EDITOR_TYPES = new Set([
	'string',
	'textbox',
	'number',
	'integer',
	'slider',
	'seed',
	'boolean',
	'checkbox',
	'select',
	'model',
	'models',
	'checkbox_group',
	'resolution',
	'lora_picker'
]);

/** Plain-words hint shown under the raw-input fallback for field types whose
 *  stored value isn't something an admin could reasonably type by hand - so
 *  the fallback (deliberately kept for these types, see `RICH_EDITOR_TYPES`'s
 *  doc comment) is never just a bare, unexplained text box. `undefined` for
 *  every type where the raw text/number/boolean/select fallback already IS a
 *  reasonable direct edit (string, seed, an unregistered plugin type, …). */
const RAW_EDITOR_HINTS: Record<string, string> = {
	carousel: "Type the option's value exactly as declared in the preset, not its label.",
	prompt_timeline: 'This field stores a structured timeline document - there is no plain-text default for it yet.',
	llm: 'This field stores structured assistant configuration - there is no plain-text default for it yet.',
	image: 'Type a file path, or leave blank - there is no upload picker in this admin view yet.',
	video: 'Type a file path, or leave blank - there is no upload picker in this admin view yet.',
	audio: 'Type a file path, or leave blank - there is no upload picker in this admin view yet.',
	media: 'Type a file path, or leave blank - there is no upload picker in this admin view yet.',
	row: 'This is a layout container, not a value field.',
	tab: 'This is a layout container, not a value field.',
	tabs: 'This is a layout container, not a value field.',
	accordion: 'This is a layout container, not a value field.',
	group: 'This is a layout container, not a value field.'
};

/** See `RAW_EDITOR_HINTS` above. */
export function rawEditorHint(fieldType: string): string | undefined {
	return RAW_EDITOR_HINTS[fieldType];
}

/** Flattens a preset form schema (`{properties: {root: {children: [...]}}}`,
 *  as returned by `GET /api/presets/{id}/form`) into a `{fieldName: config}`
 *  map. First-seen wins on a duplicate name (shouldn't happen within one
 *  schema, but matches the backend inventory's own tie-break). */
export function buildFieldConfigIndex(formSchema: unknown): Record<string, FieldConfig> {
	const index: Record<string, FieldConfig> = {};
	for (const field of extractAllFields(formSchema)) {
		if (field.name && !(field.name in index)) index[field.name] = field;
	}
	return index;
}

/** True when the field's real /generate component can be embedded as the
 *  default-value editor: the type is on the proven `RICH_EDITOR_TYPES`
 *  allow-list, a component is actually registered for it, and richer config
 *  metadata was found for it in the form schema. */
export function canUseRichEditor(fieldType: string, fieldConfig: FieldConfig | undefined): boolean {
	return !!fieldConfig && RICH_EDITOR_TYPES.has(fieldType) && hasFieldComponent(fieldType);
}

/** Wire value (what the override stores/sends) -> the shape the field's own
 *  component expects as its `value` prop. */
export function toComponentValue(fieldType: string, rawValue: unknown): unknown {
	if (MODEL_REF_TYPES.has(fieldType)) {
		return { modelPath: typeof rawValue === 'string' ? rawValue : '', tagFilters: [] };
	}
	return rawValue;
}

/** The field component's `onChange` value -> the wire value to store in the
 *  override (same shape a preset's own `default:` or the old raw-text input
 *  would have written). */
export function fromComponentValue(fieldType: string, componentValue: unknown): unknown {
	if (MODEL_REF_TYPES.has(fieldType)) {
		if (componentValue && typeof componentValue === 'object') {
			return (componentValue as { modelPath?: string }).modelPath ?? '';
		}
		return componentValue ?? '';
	}
	return componentValue;
}
