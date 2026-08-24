/**
 * Svelte context for a `section` field's persisted fold state, set by
 * `DynamicForm.svelte` (its optional `sectionCollapsedContext` prop) and read
 * by `SectionField.svelte` to look up/store its remembered collapsed state,
 * keyed by the section's structural `fieldPath` (see FieldChildren.svelte).
 *
 * Optional by design: `DynamicForm` is used outside the generate page too, so
 * when no implementation is supplied `getContext` returns `undefined` and a
 * section falls back to purely local, unpersisted fold state - see
 * activeTabContext.ts for the same "ambient, optional" pattern.
 */
import type { Readable } from 'svelte/store';

export interface SectionCollapsedContext {
	/** Remembered fold states for the tab's current preset+mode, keyed by bare
	 *  fieldPath; a missing entry means nothing is remembered yet and the caller
	 *  falls back to the YAML default.
	 *
	 *  A store rather than a getter on purpose: a plain `get()` backed by
	 *  `svelte/store`'s one-shot `get()` never registers as a dependency, so a
	 *  section's `collapsed` would not re-derive after its own toggle wrote the
	 *  map - the fold only appeared after a page reload. */
	folded: Readable<Record<string, boolean>>;
	set(fieldPath: string, collapsed: boolean): void;
}

export const SECTION_COLLAPSED_CONTEXT_KEY = 'sectionCollapsed';
