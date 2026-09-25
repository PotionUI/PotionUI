export interface DetailFooterInput {
	dirtyCount: number;
	mode: 'edit' | 'create';
	saving: boolean;
	canSave: boolean;
}

export interface DetailFooterState {
	dirtyLabel: string | null;
	saveLabel: string;
	discardLabel: string;
	saveDisabled: boolean;
	discardDisabled: boolean;
}

export function detailFooterState({ dirtyCount, mode, saving, canSave }: DetailFooterInput): DetailFooterState {
	const creating = mode === 'create';
	const dirty = dirtyCount > 0;
	return {
		dirtyLabel: dirty ? `${dirtyCount} unsaved change${dirtyCount === 1 ? '' : 's'}` : null,
		saveLabel: creating ? 'Create' : 'Save',
		discardLabel: creating ? 'Cancel' : 'Discard',
		saveDisabled: saving || !canSave || (!creating && !dirty),
		discardDisabled: saving || (!creating && !dirty)
	};
}
