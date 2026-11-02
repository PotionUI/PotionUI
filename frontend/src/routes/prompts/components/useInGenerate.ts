import type { RichSegment } from '$lib/types/segments';
import type { GenerationTab } from '$lib/types/tabs';
import { applySegmentList } from '$lib/utils/richSegments';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { mergeVariables } from '$lib/utils/variablesTransfer';
import type { VariablesMap } from '$lib/utils/variableDefs';

export interface UseInGenerateUpdate {
	updates: Partial<GenerationTab>;
	importedVariables: number;
	message: string;
}

export function buildUseInGenerateUpdate(
	tab: Pick<GenerationTab, 'promptSegments' | 'negativePromptSegments' | 'variables'>,
	segments: RichSegment[],
	variables: VariablesMap | null | undefined,
	usageHint: string,
	sourcePromptId: string | null
): UseInGenerateUpdate {
	const isNegative = usageHint === 'negative';
	const currentList = (isNegative ? tab.negativePromptSegments : tab.promptSegments) ?? [];
	const nextList = applySegmentList(currentList, segments, 'replace');
	const resolved = resolvePromptSegments(nextList);
	const updates: Partial<GenerationTab> = isNegative
		? { negativePromptSegments: nextList, negativePrompt: resolved, sourcePromptId }
		: { promptSegments: nextList, prompt: resolved, sourcePromptId };
	const importedVariables = variables ? Object.keys(variables).length : 0;
	if (importedVariables > 0) {
		updates.variables = mergeVariables(tab.variables ?? {}, variables as VariablesMap, 'replace');
	}
	const message =
		importedVariables > 0
			? `Prompt applied with ${importedVariables} variable${importedVariables === 1 ? '' : 's'}`
			: 'Prompt applied to the active Generate tab';
	return { updates, importedVariables, message };
}
