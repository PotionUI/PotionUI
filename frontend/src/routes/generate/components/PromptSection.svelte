<script lang="ts">
	import { setContext } from 'svelte';
	import { LIVE_SYNC_TAB_CONTEXT_KEY } from '$lib/stores/liveSyncAnchors';
	import LiveSyncPending from '$lib/components/session/LiveSyncPending.svelte';
	import { writable } from 'svelte/store';
	import { tabsStore } from '$lib/stores/tabs';
	import type { VariableRoll } from '$lib/utils/variableDefs';
	import type { Segment } from '$lib/types/segments';
	import { activeLoraTriggersForTab } from '$lib/stores/activeLoraTriggers';
	import type { GenerationTab, DirectorRunState } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { RegionalCaps } from '$lib/regional/regions';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import type { PresetStyle } from '$lib/types/api';
	import { appliedStyleTag, applyStyleToPrompt, clearStyle } from '$lib/prompt/styleSegments';
	import { countUsagesInSegments } from '$lib/utils/promptVariables';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import PromptTabs from '$lib/components/PromptTabs.svelte';
	import { DEFAULT_PROMPT_PLACEHOLDER, NO_PROMPT_COPY, promptTitle, type PromptCopy } from '$lib/utils/promptModeCopy';
	import { Alert, Button, SegmentedControl } from '$lib/components/ui';
	import {
		BATCH_CAP_MESSAGE,
		activePromptIndex,
		addPrompt,
		batchSummary,
		deletePrompt,
		duplicatePrompt,
		promptCount,
		promptList,
		quantityOf,
		renamePrompt,
		selectPrompt,
		type PromptListPatch
	} from '$lib/generation/promptBatch';
	import RegionalPromptEditor from '$lib/components/regional/RegionalPromptEditor.svelte';
	import { compileMossPrompt } from '$lib/regional/compile';
	import {
		REGIONAL_FORM_KEY,
		parseCanvasSize,
		readRegionalValue,
		regionalSwitchCount,
		type Region
	} from '$lib/regional/regions';
	import PromptRelayEditor from '$lib/components/PromptRelayEditor.svelte';
	import VideoDirectorEditor from '$lib/components/video-director/VideoDirectorEditor.svelte';
	import MusicDirectorEditor from '$lib/components/music-director/MusicDirectorEditor.svelte';
	import VariableManagerModal from '$lib/components/VariableManagerModal.svelte';
	import { conditionReadersFor } from '$lib/utils/conditionTags';
	import StylesPicker from '$lib/components/StylesPicker.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ResolvedPromptPreview from '$lib/components/ResolvedPromptPreview.svelte';
	import { getPresetPromptResources } from '$lib/utils/presetPromptResourcesCache';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import KitLoadHost from '$lib/components/kits/KitLoadHost.svelte';
	import KitSessionNotice from '$lib/components/kits/KitSessionNotice.svelte';
	import KitSkippedLine from '$lib/components/kits/KitSkippedLine.svelte';
	import { getPresetPromptSyntax } from '$lib/utils/presetPromptSyntaxCache';
	import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
	import { authStore } from '$lib/stores/auth';
	import { getPresetFixedPrompt } from '$lib/utils/presetFixedPromptCache';
	import { fixedPromptForMode } from '$lib/utils/presetFixedPrompt';
	import type { PresetFixedPrompt } from '$lib/types/api';

	type TabHandlers = {
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
	};

	const EMPTY_VARIABLES: VariablesMap = {};
	const EMPTY_ROLLS: Record<string, VariableRoll> = {};
	const EMPTY_FORM_DATA: Record<string, unknown> = {};
	const EMPTY_SEGMENTS: Segment[] = [];
	const EMPTY_RESOURCE_SPECS: PromptResourceSpec[] = [];
	const EMPTY_FIELD_LABELS: Record<string, string> = {};
	const EMPTY_SYNTAX_SPECS: PromptSyntaxSpec[] = [];

	let {
		tab,
		tabHandlers,
		promptRelayActive,
		regionalCaps = null,
		videoDirectorActive = false,
		videoDirectorCaps = null,
		directorRuns = undefined,
		onDirectorCheckedChange = undefined,
		onDirectorGenerateShots = undefined,
		musicDirectorActive = false,
		musicDirectorCaps = null,
		canBatch = false,
		negativePromptSupported = true,
		negativeInert = false,
		presetSegmentTemplates = [],
		presetStyles = [],
		promptCopy = NO_PROMPT_COPY,
		spacingClass = 'mt-4'
	}: {
		tab: GenerationTab;
		tabHandlers: TabHandlers;
		promptRelayActive: boolean;
		regionalCaps?: RegionalCaps | null;
		videoDirectorActive?: boolean;
		videoDirectorCaps?: DirectorCapabilities | null;
		directorRuns?: Record<string, DirectorRunState> | undefined;
		onDirectorCheckedChange?: ((checked: Set<string>) => void) | undefined;
		onDirectorGenerateShots?: ((shotIds: string[]) => void) | undefined;
		musicDirectorActive?: boolean;
		musicDirectorCaps?: MusicDirectorCapabilities | null;
		canBatch?: boolean;
		negativePromptSupported?: boolean;
		negativeInert?: boolean;
		presetSegmentTemplates?: PresetSegmentTemplate[];
		presetStyles?: PresetStyle[];
		promptCopy?: PromptCopy;
		spacingClass?: string;
	} = $props();

	setContext(LIVE_SYNC_TAB_CONTEXT_KEY, {
		get tabId() {
			return tab.id;
		}
	});

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
	const prompts = $derived(promptList(tab));
	const unsupportedNote = $derived(
		`Your other ${promptCount(tab) - 1} ${promptCount(tab) === 2 ? 'prompt is' : 'prompts are'} kept, but only prompt 1 will be generated.`
	);
	const activePrompt = $derived(activePromptIndex(tab));
	const summary = $derived(
		batchSummary({ prompts: promptCount(tab), perPrompt: quantityOf(tab.formData), canBatch })
	);
	const formData = $derived(tab.formData);
	const videoDirectorValue = $derived(tab.videoDirector);
	const musicDirectorValue = $derived(tab.musicDirector);
	const promptRelayValue = $derived(tab.promptRelay);

	const regionalValue = $derived(regionalCaps ? readRegionalValue(tab.formData) : null);
	const regionalOn = $derived(!!regionalCaps && !!regionalValue?.enabled);
	const canvasSize = $derived(parseCanvasSize(regionalCaps ? tab.formData?.[regionalCaps.sizeField] : undefined));
	const regionalCompile = $derived(
		regionalOn && regionalValue
			? (resolved: string) => compileMossPrompt(resolved, regionalValue.items, canvasSize.width, canvasSize.height)
			: null
	);
	const regionalItems = [
		{ id: 'text', label: 'Text' },
		{ id: 'regional', label: 'Regional' }
	];
	const regionalSwitchItems = $derived(
		regionalItems.map((item) =>
			item.id === 'regional' && regionalValue ? { ...item, count: regionalSwitchCount(regionalValue) } : item
		)
	);

	function writeRegional(next: { enabled: boolean; items: Region[] }) {
		tabsStore.updateTab(tabId, { formData: { ...(tab.formData || {}), [REGIONAL_FORM_KEY]: next } });
	}

	const variableCount = $derived(Object.keys(variables).length);
	const usageCounts = $derived(
		countUsagesInSegments(promptSegments, negativePromptSegments)
	);
	const conditionReaders = $derived(
		conditionReadersFor([...promptSegments, ...negativePromptSegments].map((segment) => segment.content))
	);
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
	let presetFixedPrompt = $state.raw<PresetFixedPrompt | null>(null);

	function writeKitField(field: string, value: unknown[]) {
		tabsStore.updateTab(tabId, { formData: { ...(tab.formData || {}), [field]: value } });
	}

	const kitCiteTexts = $derived([...promptSegments, ...negativePromptSegments].map((segment) => segment.content));

	const isAdmin = $derived($authStore.user?.account_type === 'ADMIN');
	const fixedPromptParts = $derived(
		isAdmin
			? fixedPromptForMode(presetFixedPrompt, selectedMode, { directorActive: videoDirectorActive || musicDirectorActive })
			: null
	);

	$effect(() => {
		const preset = selectedPreset;
		if (!isAdmin || !preset) {
			presetFixedPrompt = null;
			return;
		}
		let live = true;
		getPresetFixedPrompt(preset).then((fixed) => {
			if (live) presetFixedPrompt = fixed;
		});
		return () => {
			live = false;
		};
	});

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

	function applyPatch(patch: PromptListPatch | null) {
		if (patch) tabsStore.updateTab(tabId, patch);
	}

	function limitToOneImage() {
		tabsStore.updateTab(tabId, { formData: { ...(tab.formData || {}), quantity: 1 } });
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
	<KitSessionNotice formData={resourceFieldValues} specs={promptResourceSpecs} onChange={writeKitField} />
	<LiveSyncPending tabId={tab.id} />
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
				placeholder={promptCopy.placeholder ?? DEFAULT_PROMPT_PLACEHOLDER}
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
			>
				{#snippet afterMain()}
					{#if regionalOn && regionalValue}
						<div class="mt-3">
							<RegionalPromptEditor
								items={regionalValue.items}
								size={canvasSize}
								onChange={(items) => writeRegional({ enabled: true, items })}
							/>
						</div>
					{/if}
				{/snippet}
				{#snippet titleExtra()}
					<PromptTabs
						{prompts}
						singleLabel={promptTitle(promptCopy)}
						active={activePrompt}
						perPrompt={summary.perPrompt}
						{canBatch}
						overCap={summary.overCap}
						maxPrompts={summary.maxPrompts}
						onSelect={(index) => applyPatch(selectPrompt(tab, index))}
						onAdd={() => applyPatch(addPrompt(tab))}
						onDuplicate={(index) => applyPatch(duplicatePrompt(tab, index))}
						onDelete={(index) => applyPatch(deletePrompt(tab, index))}
						onRename={(index, name) => applyPatch(renamePrompt(tab, index, name))}
					>
						{#snippet trailing()}
							{#if regionalCaps && regionalValue}
								<span class="ml-1 flex flex-none items-center self-center">
									<SegmentedControl
										variant="toggle"
										ariaLabel="Prompt mode"
										items={regionalSwitchItems}
										selected={regionalOn ? 'regional' : 'text'}
										onSelect={(id) => writeRegional({ enabled: id === 'regional', items: regionalValue.items })}
									/>
								</span>
							{/if}
						{/snippet}
					</PromptTabs>
				{/snippet}
			</SegmentedPromptEditor>
			<div class="mt-3 space-y-2 empty:hidden">
				<KitSkippedLine texts={kitCiteTexts} specs={promptResourceSpecs} formData={resourceFieldValues} presetId={selectedPreset || null} mode={selectedMode || null} />
			</div>
			{#if summary.overCap}
				<Alert variant="danger" density="compact" icon class="mt-4">
					<span class="font-medium">{BATCH_CAP_MESSAGE}</span>
					Remove a prompt{summary.perPrompt > 1 ? ' or lower images per prompt to 1' : ''}.
					{#snippet actions()}
						<div class="flex flex-wrap justify-end gap-2">
							<Button variant="secondary" size="sm" onclick={() => applyPatch(deletePrompt(tab, summary.prompts - 1))}>
								Remove prompt {summary.prompts}
							</Button>
							{#if summary.perPrompt > 1}
								<Button variant="secondary" size="sm" onclick={limitToOneImage}>Make 1 image per prompt</Button>
							{/if}
						</div>
					{/snippet}
				</Alert>
			{:else if summary.unsupported}
				<Alert variant="warning" density="compact" icon class="mt-4">
					This preset runs one prompt at a time. {unsupportedNote}
				</Alert>
			{/if}
			<div class="mt-4">
				<ResolvedPromptPreview
					prompt={tab.prompt}
					negativePrompt={tab.negativePrompt}
					promptResources={promptResourceSpecs}
					{resourceFieldValues}
					promptSyntax={promptSyntaxSpecs}
					{variables}
					compilePrompt={regionalCompile}
					fixedPrompt={fixedPromptParts}
					presetId={selectedPreset || null}
					mode={selectedMode || null}
				/>
			</div>
		</div>
	{/if}
</div>

<KitLoadHost formData={resourceFieldValues} specs={promptResourceSpecs} onChange={writeKitField} />

<VariableManagerModal
	isOpen={variablesModalOpen}
	variables={variables}
	{usageCounts}
	readers={conditionReaders}
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
