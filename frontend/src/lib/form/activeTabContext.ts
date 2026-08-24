/**
 * Svelte context key for the id of the generation tab a `DynamicForm` instance
 * belongs to, set by `DynamicForm.svelte` (its `tabId` prop) and read by field
 * components that need to scope cross-field state to their own tab — e.g.
 * `LoraPickerField.svelte` registering its trigger words per-tab (see
 * `$lib/stores/activeLoraTriggers.ts`). Every generation tab keeps its own
 * `DynamicForm` mounted at once (tabs are hidden with CSS, not unmounted — see
 * `generate/+page.svelte`), so this can't be inferred from "the active tab".
 * A context store is used instead of prop-drilling `tabId` through every
 * row/group/accordion/tabs container between DynamicForm and the leaf field.
 */
export const ACTIVE_TAB_ID_CONTEXT_KEY = 'activeTabId';
