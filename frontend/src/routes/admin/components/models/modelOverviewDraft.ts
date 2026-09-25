
export interface ModelOverviewDraft {
	description: string;
	promptingGuidance: string;
}

export function overviewDraftFromModel(
	model: { description?: string | null; prompting_guidance?: string | null } | null
): ModelOverviewDraft {
	return {
		description: model?.description ?? '',
		promptingGuidance: model?.prompting_guidance ?? ''
	};
}

export function overviewDraftIsDirty(draft: ModelOverviewDraft, snapshot: ModelOverviewDraft): boolean {
	return draft.description !== snapshot.description || draft.promptingGuidance !== snapshot.promptingGuidance;
}
