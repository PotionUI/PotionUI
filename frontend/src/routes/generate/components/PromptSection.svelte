<script lang="ts">
	import { setContext } from 'svelte';
	import { writable } from 'svelte/store';
	import { tabsStore } from '$lib/stores/tabs';
	import type { VariableRoll } from '$lib/utils/variableDefs';
	import type { Segment } from '$lib/types/segments';
	import { activeLoraTriggersForTab } from '$lib/stores/activeLoraTriggers';
	import type { Tab, DirectorRunState } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import type { PresetStyle } from '$lib/types/api';
	import { appliedStyleTag, applyStyleToPrompt, clearStyle } from '$lib/prompt/styleSegments';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import MultiPromptEditor from '$lib/components/MultiPromptEditor.svelte';
	import PromptRelayEditor from '$lib/components/PromptRelayEditor.svelte';
	import VideoDirectorEditor from '$lib/components/video-director/VideoDirectorEditor.svelte';
	import MusicDirectorEditor from '$lib/components/music-director/MusicDirectorEditor.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import StylesPicker from '$lib/components/StylesPicker.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ResolvedPromptPreview from '$lib/components/ResolvedPromptPreview.svelte';
	import { getPresetPromptResources } from '$lib/utils/presetPromptResourcesCache';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import { getPresetPromptSyntax } from '$lib/utils/presetPromptSyntaxCache';
	import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';

	type TabHandlers = {
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
		handlePromptTabsChange: (promptTabs: any[]) => void;
		handleActivePromptTabChange: (activePromptTab: number) => void;
	};

	const EMPTY_VARIABLES: VariablesMap = {};
	const EMPTY_ROLLS: Record<string, VariableRoll> = {};
	const EMPTY_FORM_DATA: Record<string, unknown> = {};
	const EMPTY_SEGMENTS: Segment[] = [];
	const EMPTY_PROMPT_TABS: NonNullable<Tab['promptTabs']> = [];
	const EMPTY_RESOURCE_SPECS: PromptResourceSpec[] = [];
	const EMPTY_FIELD_LABELS: Record<string, string> = {};
	const EMPTY_SYNTAX_SPECS: PromptSyntaxSpec[] = [];

	let {
		tab,
		tabHandlers,
		promptRelayActive,
		videoDirectorActive = false,
		videoDirectorCaps = null,
		directorRuns = undefined,
		onDirectorCheckedChange = undefined,
		onDirectorGenerateShots = undefined,
		musicDirectorActive = false,
		musicDirectorCaps = null,
		numPrompts,
		negativePromptSupported = true,
		negativeInert = false,
		presetSegmentTemplates = [],
		presetStyles = [],
		spacingClass = 'mt-4'
	}: {
		tab: Tab;
		tabHandlers: TabHandlers;
		promptRelayActive: boolean;
		videoDirectorActive?: boolean;
		videoDirectorCaps?: DirectorCapabilities | null;
		directorRuns?: Record<string, DirectorRunState> | undefined;
		onDirectorCheckedChange?: ((checked: Set<string>) => void) | undefined;
		onDirectorGenerateShots?: ((shotIds: string[]) => void) | undefined;
		musicDirectorActive?: boolean;
		musicDirectorCaps?: MusicDirectorCapabilities | null;
		numPrompts: number;
		negativePromptSupported?: boolean;
		negativeInert?: boolean;
		presetSegmentTemplates?: PresetSegmentTemplate[];
		presetStyles?: PresetStyle[];
		spacingClass?: string;
	} = $props();

	const presetSegmentTemplatesContext = writable<PresetSegmentTemplate[]>([]);
	setContext('presetSegmentTemplates', presetSegmentTemplatesContext);
	$effect.pre(() => {
		presetSegmentTemplatesContext.set(presetSegmentTemplates);
	});

	let variablesModalOpen = $state(false);
	let stylesPickerOpen = $state(false);

	const tabId = $derived(tab.id);
	const selectedPreset = $derived(tab.selectedPreset);
	const selectedMode = $derived(tab.selectedMode);
	const selectedVariant = $derived(tab.selectedVariant ?? undefined);
	const variables = $derived(tab.variables || EMPTY_VARIABLES);
	const variableRolls = $derived(tab.variableRolls || EMPTY_ROLLS);
	const resourceFieldValues = $derived(tab.formData || EMPTY_FORM_DATA);
	const promptSegments = $derived(tab.promptSegments || EMPTY_SEGMENTS);
	const negativePromptSegments = $derived(tab.negativePromptSegments || EMPTY_SEGMENTS);
	const promptTabs = $derived(tab.promptTabs || EMPTY_PROMPT_TABS);
	const activePromptTab = $derived(tab.activePromptTab || 0);
	const formData = $derived(tab.formData);
	const videoDirectorValue = $derived(tab.videoDirector);
	const musicDirectorValue = $derived(tab.musicDirector);
	const promptRelayValue = $derived(tab.promptRelay);

	const variableCount = $derived(Object.keys(variables).length);
	const appliedStyleTagValue = $derived(appliedStyleTag(promptSegments));
	const appliedStyleId = $derived(
		appliedStyleTagValue && appliedStyleTagValue.presetId === selectedPreset ? appliedStyleTagValue.styleId : null
	);
	const appliedStyleName = $derived(
		appliedStyleId ? presetStyles.find((s) => s.id === appliedStyleId)?.name ?? null : null
	);
	const activeTriggerWordsStore = $derived(activeLoraTriggersForTab(tabId));
	const activeTriggerWords = $derived($activeTriggerWordsStore);
	const openStyles = $derived(presetStyles.length > 0 ? () => (stylesPickerOpen = true) : null);

	let promptResourceSpecs = $state.raw<PromptResourceSpec[]>(EMPTY_RESOURCE_SPECS);
	let promptResourceFieldLabels = $state.raw<Record<string, string>>(EMPTY_FIELD_LABELS);
	let promptSyntaxSpecs = $state.raw<PromptSyntaxSpec[]>(EMPTY_SYNTAX_SPECS);

	$effect.pre(() => {
		const preset = selectedPreset;
		const mode = selectedMode;
		const formName = selectedVariant;
		if (!preset || !mode) {
			promptResourceSpecs = EMPTY_RESOURCE_SPECS;
			promptResourceFieldLabels = EMPTY_FIELD_LABELS;
			promptSyntaxSpecs = EMPTY_SYNTAX_SPECS;
			return;
		}
		let live = true;
		getPresetPromptResources(preset, mode, formName).then((result) => {
			if (!live) return;
			promptResourceSpecs = result.specs;
			promptResourceFieldLabels = result.fieldLabels;
		});
		getPresetPromptSyntax(preset, mode, formName).then((specs) => {
			if (live) promptSyntaxSpecs = specs;
		});
		return () => {
			live = false;
		};
	});

	function handleVariablesChange(vars: VariablesMap) {
		tabsStore.updateTab(tabId, { variables: vars });
	}

	function handleVariableDefChange(name: string, def: VariableDef) {
		tabsStore.updateTab(tabId, { variables: { ...(tab.variables || {}), [name]: def } });
	}

	function handleSourcePromptChange(id: string | null) {
		tabsStore.updateTab(tabId, { sourcePromptId: id });
	}

	function openVariableManager() {
		variablesModalOpen = true;
	}

	function applyStyle(style: PresetStyle) {
		if (!tab.selectedPreset) return;
		const result = applyStyleToPrompt(tab.promptSegments || [], tab.negativePromptSegments || [], tab.selectedPreset, style);
		tabHandlers.handlePromptSegmentsChange(result.promptSegments);
		tabHandlers.handleNegativePromptSegmentsChange(result.negativeSegments);
	}

	function clearAppliedStyle() {
		const result = clearStyle(tab.promptSegments || [], tab.negativePromptSegments || []);
		tabHandlers.handlePromptSegmentsChange(result.promptSegments);
		tabHandlers.handleNegativePromptSegmentsChange(result.negativeSegments);
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
				onclick={() => (variablesModalOpen = true)}
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
			value={videoDirectorValue}
			capabilities={videoDirectorCaps}
			presetId={selectedPreset || ''}
			selectedVariant={tab.selectedVariant}
			selectedMode={selectedMode}
			{formData}
			runs={directorRuns}
			variables={variables}
			variableRolls={variableRolls}
			onVariableDefChange={handleVariableDefChange}
			onVariablesImport={handleVariablesChange}
			onChange={(v) => tabsStore.updateTab(tabId, { videoDirector: v })}
			onOpenVariables={openVariableManager}
			{variableCount}
			onCheckedChange={onDirectorCheckedChange}
			onGenerateShots={onDirectorGenerateShots}
			promptResources={promptResourceSpecs}
			resourceFieldLabels={promptResourceFieldLabels}
			promptSyntax={promptSyntaxSpecs}
		/>
	{:else if musicDirectorActive && musicDirectorCaps}
		<!-- Music Director Mode -->
		<MusicDirectorEditor
			value={musicDirectorValue}
			capabilities={musicDirectorCaps}
			onChange={(v) => tabsStore.updateTab(tabId, { musicDirector: v })}
		/>
	{:else if promptRelayActive}
		<!-- Prompt Relay (timeline) Mode -->
		<PromptRelayEditor
			value={promptRelayValue}
			on:change={(e) => tabsStore.updateTab(tabId, { promptRelay: e.detail })}
		/>
	{:else if numPrompts > 1}
		<!-- Multi-Prompt Editor -->
		<MultiPromptEditor
			promptTabs={promptTabs}
			activeTab={activePromptTab}
			{numPrompts}
			variables={variables}
			variableRolls={variableRolls}
			onVariableDefChange={handleVariableDefChange}
			onVariablesImport={handleVariablesChange}
			onSourcePromptChange={handleSourcePromptChange}
			onOpenVariableManager={openVariableManager}
			{activeTriggerWords}
			{presetSegmentTemplates}
			promptResources={promptResourceSpecs}
			{resourceFieldValues}
			resourceFieldLabels={promptResourceFieldLabels}
			promptSyntax={promptSyntaxSpecs}
			plain
			on:tabsChange={(e) => tabHandlers.handlePromptTabsChange(e.detail)}
			on:activeTabChange={(e) => tabHandlers.handleActivePromptTabChange(e.detail)}
		/>
	{:else}
		<!-- Single Prompt Mode (Default) -->
		<div class="prompt-composer">
			<SegmentedPromptEditor
				segments={promptSegments}
				isNegative={false}
				negativeSegments={negativePromptSegments}
				negativePromptUnavailable={!negativePromptSupported}
				negativeInert={negativeInert}
				showPreview={false}
				variables={variables}
				variableRolls={variableRolls}
				onVariableDefChange={handleVariableDefChange}
				onVariablesImport={handleVariablesChange}
				onSourcePromptChange={handleSourcePromptChange}
				onOpenVariableManager={openVariableManager}
				onOpenStyles={openStyles}
				{appliedStyleName}
				{activeTriggerWords}
				{presetSegmentTemplates}
				promptResources={promptResourceSpecs}
				{resourceFieldValues}
				resourceFieldLabels={promptResourceFieldLabels}
				promptSyntax={promptSyntaxSpecs}
				plain
				on:segmentsChange={(e) => tabHandlers.handlePromptSegmentsChange(e.detail)}
				on:negativeSegmentsChange={(e) => tabHandlers.handleNegativePromptSegmentsChange(e.detail)}
			/>
			<div class="mt-4">
				<ResolvedPromptPreview
					prompt={tab.prompt}
					negativePrompt={tab.negativePrompt}
					promptResources={promptResourceSpecs}
					{resourceFieldValues}
					promptSyntax={promptSyntaxSpecs}
				/>
			</div>
		</div>
	{/if}
</div>

<VariableManagerModal
	isOpen={variablesModalOpen}
	variables={variables}
	on:close={() => (variablesModalOpen = false)}
	on:change={(e) => handleVariablesChange(e.detail)}
/>

{#if presetStyles.length > 0}
	<StylesPicker
		isOpen={stylesPickerOpen}
		presetId={selectedPreset || ''}
		styles={presetStyles}
		{appliedStyleId}
		onClose={() => (stylesPickerOpen = false)}
		onApply={applyStyle}
		onClear={clearAppliedStyle}
	/>
{/if}

<style>
	.prompt-composer {
		container-type: inline-size;
	}
</style>
