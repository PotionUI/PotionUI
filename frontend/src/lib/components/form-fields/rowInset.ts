/**
 * Svelte context carrying the horizontal padding a `row` field's ancestors
 * have already spent on the pane's fixed content width (see
 * generationLayout.ts's `settingsPaneContentWidth`). `SectionField.svelte`'s
 * children well (`p-3`, i.e. 24px of left+right padding) narrows the space
 * available to anything nested inside it, but `RowField.svelte` computes its
 * grid from the pane width alone - this context lets it subtract what its
 * ancestors already consumed. Nested sections accumulate: each one adds its
 * own well on top of whatever inset it read from its parent.
 */
export const ROW_INSET_CONTEXT_KEY = 'rowInset';

/** Horizontal padding of SectionField's children well: `p-3` on both sides. */
export const SECTION_WELL_INSET = 24;

/** Adds a container's own inset on top of whatever its ancestors already
 *  contributed. `parent` is `undefined` when no ancestor set the context. */
export function accumulatedInset(parent: number | undefined, own: number): number {
	return (parent ?? 0) + own;
}
