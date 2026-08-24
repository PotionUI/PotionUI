/**
 * Single source of truth for what a click/Enter on a row in the @-mention
 * dropdown (AutocompleteDropdown, driven by ChatChipInput) does. Both the
 * dropdown's row click handler and the composer's keyboard-Enter path must
 * call this instead of re-deriving the decision inline — a prior duplicated
 * version of this check (one copy per call site) let 'attach a category'
 * silently regress 'attach a value' when only one copy was updated. Keeping
 * one function makes every combination testable and impossible to diverge.
 */

export type MentionRowAction = 'attach-category' | 'attach-value' | 'browse';

export interface MentionRowLike {
	hasChildren: boolean;
	/** Only meaningful when hasChildren is true; ignored for leaves. */
	attachable?: boolean;
}

export function resolveMentionRowAction(item: MentionRowLike): MentionRowAction {
	if (!item.hasChildren) return 'attach-value';
	return item.attachable ? 'attach-category' : 'browse';
}
