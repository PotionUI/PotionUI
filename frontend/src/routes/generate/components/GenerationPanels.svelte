<script lang="ts">
	import { onDestroy } from 'svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PresetControls from './PresetControls.svelte';
	import GenerationFormPane from './GenerationFormPane.svelte';
	import GenerationWorkbenchPane from './GenerationWorkbenchPane.svelte';
	import PromptSection from './PromptSection.svelte';
	import FloatingGenerationForm from './FloatingGenerationForm.svelte';
	import FloatingWorkbench from './FloatingWorkbench.svelte';
	import { Kbd, IconButton } from '$lib/components/ui';
	import { PROMPT_PANEL_MIN_WIDTH } from '$lib/stores/generationLayout';
	import { tabsStore } from '$lib/stores/tabs';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import { closeFloatingForm } from '$lib/generation/floatingForm';
	import { closeFloatingWorkbench } from '$lib/generation/floatingWorkbench';
	import type { Tab } from '$lib/types/tabs';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';

	// Desktop panel layout for a single tab. Two modes, per-tab (toggle lives
	// in the tabs-row overflow menu, persisted with the tab):
	// - 'two':   form | workbench with prompts stacked below it
	// - 'three': form | prompts | workbench — keeps prompts visible beside
	//            tall portrait (9:16) media instead of below the fold.
	// The dynamic form always stays in the leftmost pane, at a fixed width per
	// viewport tier (leftPanelWidth), with a collapse toggle — not resizable
	// Only the prompts/workbench split below stays user-resizable.
	export let tab: Tab;
	export let tabHandlers: any;
	export let promptRelayActive: boolean;
	export let videoDirectorActive: boolean = false;
	export let videoDirectorCaps: DirectorCapabilities | null = null;
	export let musicDirectorActive: boolean = false;
	export let musicDirectorCaps: MusicDirectorCapabilities | null = null;
	export let numPrompts: number;
	export let negativePromptSupported = true;
	export let negativeInert = false;
	// Promptless modes (upscale, slow-motion, …) hide the prompt pane entirely.
	// In three-pane layout this degrades to the two-pane arrangement (form |
	// workbench) with the workbench taking the freed space.
	export let promptless = false;
	export let isActive: boolean;
	export let leftPanelWidth: number;
	// Shared object reference with the page so bind:this here is visible there
	// (handlePresetReload reads dynamicFormRefs[tabId].forceReload()).
	export let dynamicFormRefs: Record<string, DynamicForm>;
	export let onFormDataChange: (data: Record<string, unknown>) => void;
	export let onWorkbenchPrevious: () => void;
	export let onWorkbenchNext: () => void;
	export let onWorkbenchHeightChange: (event: CustomEvent<string>) => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;

	// The preset card + mode/variant selectors mount at the top of this
	// settings pane, above DynamicForm (previously a full-width bar above the
	// tabs, owned by the now-deleted PresetSessionBar).
	export let presets: PresetInfo[] = [];
	export let readiness: ReadinessReport | null = null;
	export let isLoading = false;
	export let isReloading = false;
	export let availableModes: Array<{
		id: string;
		label: string;
		variants?: PresetModeVariant[];
		sourcePlugin?: string | null;
	}> = [];
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;

	// Prompts-pane resize (three-pane mode). Width persists per-tab; the drag
	// measures against the pane's own left edge so it works regardless of
	// sidebar/form widths.
	let panelsEl: HTMLDivElement;
	let promptPaneEl: HTMLDivElement;
	let isResizingPrompt = false;
	const WORKBENCH_MIN_WIDTH = 320;
	const RESIZE_HANDLE_WIDTH = 4;
	$: formPanelWidth = tab.leftPanelCollapsed ? '0.75rem' : `min(${leftPanelWidth}px, 45vw)`;
	$: floatingPresetName = presets.find((p) => p.id === tab.selectedPreset)?.name;

	// The floating workbench (FE-179) opens at the inline pane's own width —
	// captured here (the pane container's width doesn't change when its
	// content swaps to the "floating" placeholder) the first time it opens,
	// then persisted so later opens reuse it instead of re-measuring.
	let measuredPaneWidth = 0;
	$: if (tab.workbenchFloating && !tab.workbenchFloatingWidth && measuredPaneWidth > 0) {
		tabsStore.updateTab(tab.id, { workbenchFloatingWidth: String(Math.round(measuredPaneWidth)) });
	}

	// Three-pane prompt pane's max-width bound: the space reserved for the
	// workbench column on its right. Collapsed, that column is the same
	// 0.75rem rail as the form's collapsed state, so the bound shrinks to
	// match and the freed width goes to prompts.
	$: workbenchBoundWidth = tab.workbenchCollapsed ? '0.75rem' : '328px';

	function toggleWorkbenchCollapsed() {
		tabsStore.updateTab(tab.id, { workbenchCollapsed: !tab.workbenchCollapsed });
	}

	function closeFloatingGenerationForm() {
		tabsStore.updateTab(tab.id, closeFloatingForm(tab));
	}

	function closeFloatingGenerationWorkbench() {
		tabsStore.updateTab(tab.id, closeFloatingWorkbench());
	}

	function resizeFloatingGenerationWorkbench(width: number) {
		tabsStore.updateTab(tab.id, { workbenchFloatingWidth: String(Math.round(width)) });
	}

	function promptWidthForClientX(clientX: number): number {
		if (!panelsEl || !promptPaneEl) return tab.promptPanelWidth;
		const promptLeft = promptPaneEl.getBoundingClientRect().left;
		const panelRight = panelsEl.getBoundingClientRect().right;
		const availableMaximum = Math.max(
			0,
			panelRight - promptLeft - WORKBENCH_MIN_WIDTH - RESIZE_HANDLE_WIDTH
		);
		const minimum = Math.min(PROMPT_PANEL_MIN_WIDTH, availableMaximum);
		return Math.min(availableMaximum, Math.max(minimum, clientX - promptLeft));
	}

	function setPromptWidth(width: number) {
		const nextWidth = promptWidthForClientX(promptPaneEl.getBoundingClientRect().left + width);
		tabsStore.updateTab(tab.id, { promptPanelWidth: Math.round(nextWidth) });
	}

	// A press on the handle that never travels is a click: it folds the
	// workbench instead of resizing, so the same strip does both jobs.
	const RESIZE_CLICK_SLOP_PX = 4;
	let promptResizeStartX = 0;
	let promptResizeMoved = false;

	function startPromptResize(event: PointerEvent) {
		event.preventDefault();
		isResizingPrompt = true;
		promptResizeStartX = event.clientX;
		promptResizeMoved = false;
		document.addEventListener('pointermove', handlePromptResize);
		document.addEventListener('pointerup', finishPromptResize);
		document.addEventListener('pointercancel', stopPromptResize);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function handlePromptResize(event: PointerEvent) {
		if (!isResizingPrompt || !promptPaneEl) return;
		if (Math.abs(event.clientX - promptResizeStartX) > RESIZE_CLICK_SLOP_PX) promptResizeMoved = true;
		if (!promptResizeMoved) return;
		setPromptWidth(event.clientX - promptPaneEl.getBoundingClientRect().left);
	}

	function handlePromptResizeKeydown(event: KeyboardEvent) {
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		event.preventDefault();
		setPromptWidth(tab.promptPanelWidth + (event.key === 'ArrowLeft' ? -24 : 24));
	}

	function stopPromptResize() {
		if (!isResizingPrompt) return;
		isResizingPrompt = false;
		document.removeEventListener('pointermove', handlePromptResize);
		document.removeEventListener('pointerup', finishPromptResize);
		document.removeEventListener('pointercancel', stopPromptResize);
		document.body.style.cursor = '';
		document.body.style.userSelect = '';
	}

	function finishPromptResize() {
		const wasResizing = isResizingPrompt;
		stopPromptResize();
		if (wasResizing && !promptResizeMoved) toggleWorkbenchCollapsed();
	}

	onDestroy(stopPromptResize);
</script>

<div bind:this={panelsEl} class="flex h-full">
	<!-- Left Panel: Form -->
	{#if tab.leftPanelCollapsed}
		<Tooltip text="Expand generation settings" kbd={$shortcutLabels['toggle_left_panel']} position="right" delay={150} wrapperClass="flex h-full flex-shrink-0">
			<button
				type="button"
				class="group flex w-3 h-full flex-shrink-0 items-center justify-center border-r border-line bg-surface-3 transition-colors hover:bg-line-hover"
				aria-label="Expand generation settings"
				aria-expanded="false"
				on:click={() => tabsStore.updateTab(tab.id, { leftPanelCollapsed: false })}
			>
				<Icon
					name="chevron-right"
					className="h-3 w-3 text-fg-subtle transition-colors group-hover:text-fg"
				/>
			</button>
		</Tooltip>
	{:else}
		<div class="flex h-full flex-shrink-0" style="width: {formPanelWidth}">
			<div class="min-w-0 flex-1 overflow-y-auto bg-surface-1/30">
			<div class="border-b border-line px-3 pb-3 pt-3">
				<PresetControls
					{tab}
					{presets}
					{readiness}
					{isLoading}
					{isReloading}
					{availableModes}
					{onPresetChange}
					{onModeChange}
					{onVariantChange}
					{onReload}
				/>
			</div>
			<div class="p-4">
				<GenerationFormPane
					bind:formRef={dynamicFormRefs[tab.id]}
					{tab}
					{videoDirectorActive}
					{onFormDataChange}
				/>
			</div>
			</div>
			<Tooltip text="Collapse generation settings" kbd={$shortcutLabels['toggle_left_panel']} position="right" delay={150} wrapperClass="flex h-full flex-shrink-0">
				<button
					type="button"
					class="group relative w-3 h-full flex-shrink-0 border-r border-line bg-surface-3 transition-colors hover:bg-line-hover"
					aria-label="Collapse generation settings"
					aria-expanded="true"
					on:click={() => tabsStore.updateTab(tab.id, { leftPanelCollapsed: true })}
				>
					<Icon
						name="chevron-left"
						className="absolute left-1/2 top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 text-fg-subtle transition-colors group-hover:text-fg"
					/>
				</button>
			</Tooltip>
		</div>
	{/if}

	{#if tab.layoutMode === 'three' && !promptless}
		<!-- Middle Panel: Prompts (resizable, grows to fill the freed space when the workbench is collapsed) -->
		<div
			bind:this={promptPaneEl}
			data-testid="prompts-pane"
			class={tab.workbenchCollapsed
				? 'flex-1 min-w-0 overflow-y-auto bg-surface-1/20'
				: 'flex-shrink-0 overflow-y-auto bg-surface-1/20'}
			style={tab.workbenchCollapsed
				? ''
				: `width: ${tab.promptPanelWidth}px; max-width: calc(100% - ${formPanelWidth} - ${workbenchBoundWidth})`}
		>
			<div class="p-4">
				<PromptSection
					{tab}
					{tabHandlers}
					{promptRelayActive}
					{videoDirectorActive}
					{videoDirectorCaps}
					{musicDirectorActive}
					{musicDirectorCaps}
					{numPrompts}
					{negativePromptSupported}
					{negativeInert}
					spacingClass=""
				/>
			</div>
		</div>

		{#if !tab.workbenchCollapsed}
			<!-- Prompts/Workbench Resize Handle -->
			<Tooltip text="Drag to resize · click to collapse" kbd={$shortcutLabels['toggle_workbench_panel']} position="left" delay={150} wrapperClass="flex h-full flex-shrink-0">
				<button
					type="button"
					class="resize-handle h-full flex-shrink-0 w-1 bg-line hover:bg-line-hover cursor-col-resize transition-colors relative group"
					on:pointerdown={startPromptResize}
					on:keydown={handlePromptResizeKeydown}
					aria-label="Resize prompt panel"
					data-testid="prompts-workbench-handle"
				>
					<div class="absolute inset-y-0 -left-1 -right-1 group-hover:bg-line-hover/20"></div>
				</button>
			</Tooltip>
		{/if}

		<!-- Right Panel: Workbench only (full height for portrait media) -->
		{#if tab.workbenchCollapsed}
			<Tooltip text="Expand workbench" kbd={$shortcutLabels['toggle_workbench_panel']} position="left" delay={150} wrapperClass="flex h-full flex-shrink-0">
				<button
					type="button"
					class="group flex w-3 h-full flex-shrink-0 items-center justify-center border-l border-line bg-surface-3 transition-colors hover:bg-line-hover"
					aria-label="Expand workbench"
					aria-expanded="false"
					data-testid="workbench-pane"
					on:click={toggleWorkbenchCollapsed}
				>
					<Icon
						name="chevron-left"
						className="h-3 w-3 text-fg-subtle transition-colors group-hover:text-fg"
					/>
				</button>
			</Tooltip>
		{:else}
			<div
				class="relative flex-1 min-w-[320px] overflow-y-auto p-4"
				data-testid="workbench-pane"
				bind:clientWidth={measuredPaneWidth}
			>
				<div class="absolute right-2 top-2 z-10">
					<Tooltip text="Collapse workbench" kbd={$shortcutLabels['toggle_workbench_panel']} position="left" delay={150}>
						<IconButton
							icon="chevron-right"
							label="Collapse workbench"
							size="sm"
							onclick={toggleWorkbenchCollapsed}
						/>
					</Tooltip>
				</div>
				{#if isActive}
					{#if tab.workbenchFloating}
						<div class="flex h-full items-center justify-center gap-2 text-fg-subtle">
							<span class="text-sm">Workbench is floating</span>
							<Kbd keys={$shortcutLabels['toggle_floating_workbench'] || 'W'} />
							<span class="text-sm">to dock</span>
						</div>
					{:else}
						<GenerationWorkbenchPane
							{tab}
							{onWorkbenchPrevious}
							{onWorkbenchNext}
							{onWorkbenchHeightChange}
							{onMoveToWorkbench}
						/>
					{/if}
				{/if}
			</div>
		{/if}
	{:else}
		<!-- Right Panel: Workbench + Prompts -->
		<div class="flex-1 min-w-0 flex flex-col overflow-hidden">
			{#if tab.workbenchCollapsed}
				<!-- Workbench Area: collapsed to a thin rail above the prompts -->
				<Tooltip text="Expand workbench" kbd={$shortcutLabels['toggle_workbench_panel']} position="bottom" delay={150}>
					<button
						type="button"
						class="group flex h-3 w-full flex-shrink-0 items-center justify-center border-b border-line bg-surface-3 transition-colors hover:bg-line-hover"
						aria-label="Expand workbench"
						aria-expanded="false"
						data-testid="workbench-pane"
						on:click={toggleWorkbenchCollapsed}
					>
						<Icon
							name="chevron-down"
							className="h-3 w-3 text-fg-subtle transition-colors group-hover:text-fg"
						/>
					</button>
				</Tooltip>

				{#if !promptless}
					<div class="flex-1 min-h-0 overflow-y-auto p-4">
						<PromptSection
							{tab}
							{tabHandlers}
							{promptRelayActive}
							{videoDirectorActive}
							{videoDirectorCaps}
							{musicDirectorActive}
							{musicDirectorCaps}
							{numPrompts}
							{negativePromptSupported}
							{negativeInert}
							spacingClass=""
						/>
					</div>
				{/if}
			{:else}
				<!-- Workbench Area -->
				<div
					class="relative flex-1 min-h-0 overflow-y-auto p-4"
					data-testid="workbench-pane"
					bind:clientWidth={measuredPaneWidth}
				>
					<div class="absolute right-2 top-2 z-10">
						<Tooltip text="Collapse workbench" kbd={$shortcutLabels['toggle_workbench_panel']} position="bottom">
							<IconButton
								icon="chevron-up"
								label="Collapse workbench"
								size="sm"
								onclick={toggleWorkbenchCollapsed}
							/>
						</Tooltip>
					</div>
					{#if isActive}
						{#if tab.workbenchFloating}
							<div class="flex h-40 items-center justify-center gap-2 text-fg-subtle">
								<span class="text-sm">Workbench is floating</span>
								<Kbd keys={$shortcutLabels['toggle_floating_workbench'] || 'W'} />
								<span class="text-sm">to dock</span>
							</div>
						{:else}
							<GenerationWorkbenchPane
								{tab}
								{onWorkbenchPrevious}
								{onWorkbenchNext}
								{onWorkbenchHeightChange}
								{onMoveToWorkbench}
							/>
						{/if}
					{/if}

					{#if !promptless}
						<PromptSection
							{tab}
							{tabHandlers}
							{promptRelayActive}
							{videoDirectorActive}
							{videoDirectorCaps}
							{musicDirectorActive}
							{musicDirectorCaps}
							{numPrompts}
							{negativePromptSupported}
							{negativeInert}
							spacingClass="mt-6"
						/>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>

{#if tab.formFloating}
	<FloatingGenerationForm
		{tab}
		presetName={floatingPresetName}
		{videoDirectorActive}
		{dynamicFormRefs}
		{onFormDataChange}
		width={leftPanelWidth}
		onClose={closeFloatingGenerationForm}
		closeShortcut={$shortcutLabels['toggle_floating_form']}
	/>
{/if}

{#if tab.workbenchFloating}
	<FloatingWorkbench
		{tab}
		{onWorkbenchPrevious}
		{onWorkbenchNext}
		{onWorkbenchHeightChange}
		{onMoveToWorkbench}
		onClose={closeFloatingGenerationWorkbench}
		onResizeWidth={resizeFloatingGenerationWorkbench}
		closeShortcut={$shortcutLabels['toggle_floating_workbench']}
	/>
{/if}

<style>
	.resize-handle {
		touch-action: none;
	}

	.resize-handle:hover,
	.resize-handle:active {
		background-color: rgb(var(--line-hover));
	}
</style>
