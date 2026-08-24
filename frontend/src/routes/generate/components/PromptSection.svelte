<script lang="ts">
	import { tabsStore } from '$lib/stores/tabs';
	import { activeLoraTriggersForTab } from '$lib/stores/activeLoraTriggers';
	import type { Tab } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import MultiPromptEditor from '$lib/components/MultiPromptEditor.svelte';
	import PromptRelayEditor from '$lib/components/PromptRelayEditor.svelte';
	import VideoDirectorEditor from '$lib/components/video-director/VideoDirectorEditor.svelte';
	import MusicDirectorEditor from '$lib/components/music-director/MusicDirectorEditor.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ResolvedPromptPreview from './ResolvedPromptPreview.svelte';

	// Renders the prompt-relay / multi-prompt / segmented-prompt-pair choice for
	// a single tab. Extracted verbatim from the mobile (Panel 2) and desktop
	// (right panel) copies in generate/+page.svelte, which were identical apart
	// from the outer spacing class.
	export let tab: Tab;
	export let tabHandlers: {
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
		handlePromptTabsChange: (promptTabs: any[]) => void;
		handleActivePromptTabChange: (activePromptTab: number) => void;
	};
	export let promptRelayActive: boolean;
	export let videoDirectorActive: boolean = false;
	export let videoDirectorCaps: DirectorCapabilities | null = null;
	export let musicDirectorActive: boolean = false;
	export let musicDirectorCaps: MusicDirectorCapabilities | null = null;
	export let numPrompts: number;
	export let negativePromptSupported = true;
	export let negativeInert = false;
	export let spacingClass: string = 'mt-4';
	let variablesModalOpen = false;

	$: variableCount = Object.keys(tab.variables || {}).length;
	// LoRA trigger words for this tab's own lora_picker field(s) — highlighted
	// inline in the segment editors below (see activeLoraTriggers.ts).
	$: activeTriggerWordsStore = activeLoraTriggersForTab(tab.id);
	$: activeTriggerWords = $activeTriggerWordsStore;

	function handleVariablesChange(vars: VariablesMap) {
		tabsStore.updateTab(tab.id, { variables: vars });
	}

	// A usage chip's popover edits ONE variable's definition — merge it into the
	// tab's map rather than replacing the whole thing, so it composes cleanly with
	// concurrent edits from the Variable Manager modal.
	function handleVariableDefChange(name: string, def: VariableDef) {
		tabsStore.updateTab(tab.id, { variables: { ...(tab.variables || {}), [name]: def } });
	}

	function openVariableManager() {
		variablesModalOpen = true;
	}
</script>

<!-- Prompt Editors -->
<div class="space-y-4 {spacingClass}">
	{#if promptRelayActive}
		<!-- PromptRelayEditor doesn't render its own toolbar/header, so it needs
			this standalone entry point. Video Director has its own header and
			adopts the same button there instead (see onOpenVariables below). -->
		<div class="flex justify-end">
			<button
				type="button"
				class="inline-flex h-8 items-center gap-1.5 rounded border border-line px-2.5 text-xs font-medium text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-2 hover:text-fg"
				on:click={() => (variablesModalOpen = true)}
			>
				<Icon name="braces" className="h-3.5 w-3.5" />
				<span>Variables</span>
				{#if variableCount > 0}
					<span class="rounded bg-signal/15 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-signal">{variableCount}</span>
				{/if}
			</button>
		</div>
	{/if}

	{#if videoDirectorActive && videoDirectorCaps}
		<!-- Video Director Mode -->
		<VideoDirectorEditor
			value={tab.videoDirector}
			capabilities={videoDirectorCaps}
			presetId={tab.selectedPreset || ''}
			formData={tab.formData}
			onChange={(v) => tabsStore.updateTab(tab.id, { videoDirector: v })}
			onOpenVariables={openVariableManager}
			{variableCount}
		/>
	{:else if musicDirectorActive && musicDirectorCaps}
		<!-- Music Director Mode -->
		<MusicDirectorEditor
			value={tab.musicDirector}
			capabilities={musicDirectorCaps}
			onChange={(v) => tabsStore.updateTab(tab.id, { musicDirector: v })}
		/>
	{:else if promptRelayActive}
		<!-- Prompt Relay (timeline) Mode -->
		<PromptRelayEditor
			value={tab.promptRelay}
			on:change={(e) => tabsStore.updateTab(tab.id, { promptRelay: e.detail })}
		/>
	{:else if numPrompts > 1}
		<!-- Multi-Prompt Editor -->
		<MultiPromptEditor
			promptTabs={tab.promptTabs || []}
			activeTab={tab.activePromptTab || 0}
			{numPrompts}
			variables={tab.variables || {}}
			variableRolls={tab.variableRolls || {}}
			onVariableDefChange={handleVariableDefChange}
			onOpenVariableManager={openVariableManager}
			{activeTriggerWords}
			on:tabsChange={(e) => tabHandlers.handlePromptTabsChange(e.detail)}
			on:activeTabChange={(e) => tabHandlers.handleActivePromptTabChange(e.detail)}
		/>
	{:else}
		<!-- Single Prompt Mode (Default) -->
		<div class="prompt-composer">
			<SegmentedPromptEditor
				segments={tab.promptSegments || []}
				isNegative={false}
				negativeSegments={tab.negativePromptSegments || []}
				negativePromptUnavailable={!negativePromptSupported}
				negativeInert={negativeInert}
				showPreview={false}
				variables={tab.variables || {}}
				variableRolls={tab.variableRolls || {}}
				onVariableDefChange={handleVariableDefChange}
				onOpenVariableManager={openVariableManager}
				{activeTriggerWords}
				on:segmentsChange={(e) => tabHandlers.handlePromptSegmentsChange(e.detail)}
				on:negativeSegmentsChange={(e) => tabHandlers.handleNegativePromptSegmentsChange(e.detail)}
			/>
			<div class="mt-4">
				<ResolvedPromptPreview prompt={tab.prompt} negativePrompt={tab.negativePrompt} />
			</div>
		</div>
	{/if}
</div>

<VariableManagerModal
	isOpen={variablesModalOpen}
	variables={tab.variables || {}}
	on:close={() => (variablesModalOpen = false)}
	on:change={(e) => handleVariablesChange(e.detail)}
/>

<style>
	.prompt-composer {
		container-type: inline-size;
	}
</style>
