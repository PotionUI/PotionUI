<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { PromptTabData } from '$lib/types/tabs';
	import type { Segment } from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import SegmentedPromptEditor from './SegmentedPromptEditor.svelte';
	import { flattenRichSegments } from '$lib/utils/richSegments';

	export let promptTabs: PromptTabData[] = [];
	export let activeTab: number = 0;
	export let numPrompts: number = 1;
	// Prompt variables (see stores/tabs.ts `Tab.variables`) — shared by every
	// prompt tab of a multi-prompt generate tab, same as the single-prompt path.
	export let variables: VariablesMap = {};
	export let variableRolls: Record<string, VariableRoll> = {};
	export let onVariableDefChange: ((name: string, def: VariableDef) => void) | undefined = undefined;
	export let onOpenVariableManager: (() => void) | undefined = undefined;
	export let activeTriggerWords: string[] = [];

	const dispatch = createEventDispatcher();

	$: {
		if (promptTabs.length < numPrompts) {
			const newTabs = [...promptTabs];
			for (let i = promptTabs.length; i < numPrompts; i++) {
				newTabs.push(createEmptyPromptTab());
			}
			dispatch('tabsChange', newTabs);
		}
	}

	function createEmptyPromptTab(): PromptTabData {
		return {
			promptSegments: [],
			negativePromptSegments: [],
			prompt: '',
			negativePrompt: ''
		};
	}

	function setActiveTab(index: number) {
		activeTab = index;
		dispatch('activeTabChange', index);
	}

	function handleSegmentsChange(tabIndex: number, segments: Segment[]) {
		const updatedTabs = [...promptTabs];
		if (updatedTabs[tabIndex]) {
			updatedTabs[tabIndex] = {
				...updatedTabs[tabIndex],
				promptSegments: segments,
				prompt: flattenRichSegments(segments)
			};
			dispatch('tabsChange', updatedTabs);
		}
	}

	function handleNegativeSegmentsChange(tabIndex: number, segments: Segment[]) {
		const updatedTabs = [...promptTabs];
		if (updatedTabs[tabIndex]) {
			updatedTabs[tabIndex] = {
				...updatedTabs[tabIndex],
				negativePromptSegments: segments,
				negativePrompt: flattenRichSegments(segments)
			};
			dispatch('tabsChange', updatedTabs);
		}
	}
</script>

<div class="multi-prompt-editor">
	{#if numPrompts > 1}
		<div class="flex gap-1 mb-3 border-b border-line">
			{#each Array(numPrompts) as _, i}
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium transition-colors rounded-t-lg
						{activeTab === i
						? 'text-signal border-b-2 border-signal bg-signal/10'
						: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
					on:click={() => setActiveTab(i)}
				>
					Prompt {i + 1}
				</button>
			{/each}
		</div>
	{/if}

	{#each Array(numPrompts) as _, i}
		<div class="prompt-tab-content" style="display: {i === activeTab ? 'block' : 'none'}">
			<SegmentedPromptEditor
				segments={promptTabs[i]?.promptSegments || []}
				isNegative={false}
				negativeSegments={promptTabs[i]?.negativePromptSegments || []}
				{variables}
				{variableRolls}
				{onVariableDefChange}
				{onOpenVariableManager}
				{activeTriggerWords}
				on:segmentsChange={(e) => handleSegmentsChange(i, e.detail)}
				on:negativeSegmentsChange={(e) => handleNegativeSegmentsChange(i, e.detail)}
			/>
		</div>
	{/each}
</div>

<style>
	.multi-prompt-editor {
		width: 100%;
	}

	.prompt-tab-content {
		animation: fadeIn 0.15s ease-in-out;
	}

	@keyframes fadeIn {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
</style>
