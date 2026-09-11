<script lang="ts">
	import { tabsStore } from '$lib/stores/tabs';
	import { activeLoraTriggersForTab } from '$lib/stores/activeLoraTriggers';
	import type { Tab, DirectorRunState } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import type { PresetStyle } from '$lib/types/api';
	import { appliedStyleTag, applyStyleToPrompt } from '$lib/prompt/styleSegments';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import MultiPromptEditor from '$lib/components/MultiPromptEditor.svelte';
	import PromptRelayEditor from '$lib/components/PromptRelayEditor.svelte';
	import VideoDirectorEditor from '$lib/components/video-director/VideoDirectorEditor.svelte';
	import MusicDirectorEditor from '$lib/components/music-director/MusicDirectorEditor.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import StylesPicker from '$lib/components/StylesPicker.svelte';
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
	/** `Tab.directorRuns` -- see VideoDirectorEditor.svelte's own doc comment. */
	export let directorRuns: Record<string, DirectorRunState> | undefined = undefined;
	/** See ShotConsole.svelte's own doc comments -- passed straight through to
	 *  VideoDirectorEditor. */
	export let onDirectorCheckedChange: ((checked: Set<string>) => void) | undefined = undefined;
	export let onDirectorGenerateShots: ((shotIds: string[]) => void) | undefined = undefined;
	export let musicDirectorActive: boolean = false;
	export let musicDirectorCaps: MusicDirectorCapabilities | null = null;
	export let numPrompts: number;
	export let negativePromptSupported = true;
	export let negativeInert = false;
	/** Segment Templates the tab's preset declares for its selected mode --
	 *  resolved on the page and merged into the apply picker here. */
	export let presetSegmentTemplates: PresetSegmentTemplate[] = [];
	/** Styles the tab's preset curates -- resolved on the page from the preset
	 *  detail response and offered through the Styles picker below. Only the
	 *  standard single-prompt segment editor supports styles today. */
	export let presetStyles: PresetStyle[] = [];
	export let spacingClass: string = 'mt-4';
	let variablesModalOpen = false;
	let stylesPickerOpen = false;

	$: variableCount = Object.keys(tab.variables || {}).length;
	// Derived from the tagged prepend/append segment pair, never tracked separately --
	// see styleSegments.ts. Deleting either card through the ordinary segment delete
	// action clears this with no extra bookkeeping.
	$: appliedStyleTagValue = appliedStyleTag(tab.promptSegments || []);
	$: appliedStyleId =
		appliedStyleTagValue && appliedStyleTagValue.presetId === tab.selectedPreset ? appliedStyleTagValue.styleId : null;
	$: appliedStyleName = appliedStyleId ? presetStyles.find((s) => s.id === appliedStyleId)?.name ?? null : null;
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

	function applyStyle(style: PresetStyle) {
		if (!tab.selectedPreset) return;
		const result = applyStyleToPrompt(tab.promptSegments || [], tab.negativePromptSegments || [], tab.selectedPreset, style);
		tabHandlers.handlePromptSegmentsChange(result.promptSegments);
		tabHandlers.handleNegativePromptSegmentsChange(result.negativeSegments);
		stylesPickerOpen = false;
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
			selectedVariant={tab.selectedVariant}
			selectedMode={tab.selectedMode}
			formData={tab.formData}
			runs={directorRuns}
			onChange={(v) => tabsStore.updateTab(tab.id, { videoDirector: v })}
			onOpenVariables={openVariableManager}
			{variableCount}
			onCheckedChange={onDirectorCheckedChange}
			onGenerateShots={onDirectorGenerateShots}
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
			{presetSegmentTemplates}
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
				onOpenStyles={presetStyles.length > 0 ? () => (stylesPickerOpen = true) : null}
				{appliedStyleName}
				{activeTriggerWords}
				{presetSegmentTemplates}
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

{#if presetStyles.length > 0}
	<StylesPicker
		isOpen={stylesPickerOpen}
		presetId={tab.selectedPreset || ''}
		styles={presetStyles}
		{appliedStyleId}
		on:close={() => (stylesPickerOpen = false)}
		on:apply={(e) => applyStyle(e.detail)}
	/>
{/if}

<style>
	.prompt-composer {
		container-type: inline-size;
	}
</style>
