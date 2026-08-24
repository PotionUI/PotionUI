/**
 * Svelte context key for the per-field server-validation-errors store set by
 * `DynamicForm.svelte` (its `fieldErrors` prop) and read by `FormField.svelte`
 * (renders messages under the field) and `TabsField.svelte` (per-tab error
 * count badge + auto-switch-to-erroring-tab). A context store is used instead
 * of prop-drilling `fieldErrors` through every row/group/accordion/tabs
 * container between DynamicForm and the leaf field.
 */
export const FORM_FIELD_ERRORS_CONTEXT_KEY = 'formFieldErrors';
