<script lang="ts">
	import { onDestroy } from 'svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PresetControls from './PresetControls.svelte';
	import GenerationFormPane from './GenerationFormPane.svelte';
	import GenerationWorkbenchPane from './GenerationWorkbenchPane.svelte';
	import PromptSection from './PromptSection.svelte';
	import { PROMPT_PANEL_MIN_WIDTH } from '$lib/stores/generationLayout';
	import { tabsStore } from '$lib/stores/tabs';
	import { shortcutLabels } from '$lib/stores/keybindings';
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

	function startPromptResize(event: PointerEvent) {
		event.preventDefault();
		isResizingPrompt = true;
		document.addEventListener('pointermove', handlePromptResize);
		document.addEventListener('pointerup', stopPromptResize);
		document.addEventListener('pointercancel', stopPromptResize);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function handlePromptResize(event: PointerEvent) {
		if (!isResizingPrompt || !promptPaneEl) return;
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
		document.removeEventListener('pointerup', stopPromptResize);
		document.removeEventListener('pointercancel', stopPromptResize);
		document.body.style.cursor = '';
		document.body.style.userSelect = '';
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
		<!-- Middle Panel: Prompts (resizable) -->
		<div
			bind:this={promptPaneEl}
			class="flex-shrink-0 overflow-y-auto bg-surface-1/20"
			style="width: {tab.promptPanelWidth}px; max-width: calc(100% - {formPanelWidth} - 328px)"
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

		<!-- Prompts/Workbench Resize Handle -->
		<button
			type="button"
			class="resize-handle flex-shrink-0 w-1 bg-line hover:bg-line-hover cursor-col-resize transition-colors relative group"
			on:pointerdown={startPromptResize}
			on:keydown={handlePromptResizeKeydown}
			aria-label="Resize prompt panel"
			title="Drag to resize prompts and workbench"
		>
			<div class="absolute inset-y-0 -left-1 -right-1 group-hover:bg-line-hover/20"></div>
		</button>

		<!-- Right Panel: Workbench only (full height for portrait media) -->
		<div class="flex-1 min-w-[320px] overflow-y-auto p-4">
			{#if isActive}
				<GenerationWorkbenchPane
					{tab}
					{onWorkbenchPrevious}
					{onWorkbenchNext}
					{onWorkbenchHeightChange}
					{onMoveToWorkbench}
				/>
			{/if}
		</div>
	{:else}
		<!-- Right Panel: Workbench + Prompts -->
		<div class="flex-1 min-w-0 flex flex-col overflow-hidden">
			<!-- Workbench Area -->
			<div class="flex-1 min-h-0 overflow-y-auto p-4">
				{#if isActive}
					<GenerationWorkbenchPane
						{tab}
						{onWorkbenchPrevious}
						{onWorkbenchNext}
						{onWorkbenchHeightChange}
						{onMoveToWorkbench}
					/>
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
		</div>
	{/if}
</div>

<style>
	.resize-handle {
		touch-action: none;
	}

	.resize-handle:hover,
	.resize-handle:active {
		background-color: rgb(var(--line-hover));
	}
</style>
