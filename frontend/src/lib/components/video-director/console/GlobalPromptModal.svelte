<script lang="ts">
	import type { VideoDirectorValue } from '$lib/types/videoDirector';
	import type { Segment } from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';

	let {
		isOpen,
		doc,
		onClose,
		onGlobalSegmentsChange,
		onNegativeSegmentsChange,
		variables = {},
		variableRolls = {},
		onVariableDefChange,
		onOpenVariableManager,
		promptResources = [],
		resourceFieldValues,
		resourceFieldLabels = {},
		promptSyntax = []
	}: {
		isOpen: boolean;
		doc: VideoDirectorValue;
		onClose: () => void;
		onGlobalSegmentsChange: (segments: Segment[]) => void;
		onNegativeSegmentsChange: (segments: Segment[]) => void;
		variables?: VariablesMap;
		variableRolls?: Record<string, VariableRoll>;
		onVariableDefChange?: (name: string, def: VariableDef) => void;
		onOpenVariableManager?: () => void;
		promptResources?: PromptResourceSpec[];
		resourceFieldValues?: Record<string, unknown> | null | undefined;
		resourceFieldLabels?: Record<string, string>;
		promptSyntax?: PromptSyntaxSpec[];
	} = $props();
</script>

<BaseModal {isOpen} title="Global prompt" subtitle="Applies to every shot in this film" size="lg" on:close={onClose}>
	<div class="flex flex-col gap-5 p-4 md:p-5">
		<SegmentedPromptEditor
			segments={doc.global_prompt_segments}
			label="Global prompt"
			showPreview={false}
			plain
			{variables}
			{variableRolls}
			{onVariableDefChange}
			{onOpenVariableManager}
			{promptResources}
			resourceFieldValues={resourceFieldValues || {}}
			{resourceFieldLabels}
			{promptSyntax}
			on:segmentsChange={(e) => onGlobalSegmentsChange(e.detail)}
		/>
		<SegmentedPromptEditor
			segments={doc.negative_prompt_segments}
			isNegative
			label="Negative prompt"
			showPreview={false}
			plain
			{variables}
			{variableRolls}
			{onVariableDefChange}
			{onOpenVariableManager}
			{promptResources}
			resourceFieldValues={resourceFieldValues || {}}
			{resourceFieldLabels}
			{promptSyntax}
			on:segmentsChange={(e) => onNegativeSegmentsChange(e.detail)}
		/>
	</div>
</BaseModal>
