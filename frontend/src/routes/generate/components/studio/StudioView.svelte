<script lang="ts">
	import type { Tab } from '$lib/types/tabs';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import { representativeDirectorPrompt } from '$lib/utils/videoDirector';
	import StudioCanvas from './StudioCanvas.svelte';
	import StudioTopBar from './StudioTopBar.svelte';
	import StudioDock from './StudioDock.svelte';
	import StudioPromptSheet from './StudioPromptSheet.svelte';
	import StudioSettingsSheet from './StudioSettingsSheet.svelte';
	import StudioPresetSessionSheet from './StudioPresetSessionSheet.svelte';
	import StudioChatSheet from './StudioChatSheet.svelte';

	export let tab: Tab;
	export let tabHandlers: {
		handlePresetChange: (presetId: string) => void;
		handleModeChange: (mode: string) => void;
		handleVariantChange: (variant: string) => void;
		handlePromptChange: (prompt: string) => void;
		handlePromptSegmentsChange: (segments: any[]) => void;
		handleNegativePromptChange: (prompt: string) => void;
		handleNegativePromptSegmentsChange: (segments: any[]) => void;
		handlePromptTabsChange: (promptTabs: any[]) => void;
		handleActivePromptTabChange: (activePromptTab: number) => void;
	};
	export let presets: any[];
	export let readiness: ReadinessReport | null;
	export let isLoading: boolean;
	export let isReloadingPreset: boolean;
	export let tabModes: any[];
	export let startGeneration: () => void;
	export let cancelGeneration: () => void;
	export let canGenerate: boolean;
	export let generateDisabledReason: string | undefined;
	export let promptRelayActive: boolean;
	export let videoDirectorActive: boolean;
	export let videoDirectorCaps: DirectorCapabilities | null;
	export let musicDirectorActive: boolean;
	export let musicDirectorCaps: MusicDirectorCapabilities | null;
	export let numPrompts: number;
	export let negativePromptSupported: boolean;
	export let negativeInert: boolean;
	export let promptlessActive: boolean;
	export let dynamicFormRefs: Record<string, DynamicForm>;
	export let onFormDataChange: (data: Record<string, unknown>) => void;
	export let onReload: () => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;

	type SheetName = 'prompt' | 'settings' | 'preset' | 'chat';
	let openSheet: SheetName | null = null;
	function closeSheet() {
		openSheet = null;
	}

	$: availableModes = tabModes.map((m) => ({
		id: m.name,
		label: m.label,
		variants: m.variants,
		sourcePlugin: m.source_plugin
	}));
	$: presetName = presets.find((p: any) => p.id === tab.selectedPreset)?.name;
	$: modeLabel = availableModes.find((m) => m.id === tab.selectedMode)?.label;

	$: dockPromptPreview =
		tab.prompt ||
		(videoDirectorActive && videoDirectorCaps && tab.videoDirector
			? representativeDirectorPrompt(tab.videoDirector, videoDirectorCaps)
			: '') ||
		'';
</script>

<div class="relative h-full w-full overflow-hidden">
	<StudioCanvas {tab} />

	<StudioTopBar
		tabName={tab.name}
		{presetName}
		{modeLabel}
		onOpenPresetSheet={() => (openSheet = 'preset')}
		onOpenChatSheet={() => (openSheet = 'chat')}
	/>

	<StudioDock
		{tab}
		promptPreviewText={dockPromptPreview}
		{promptlessActive}
		{canGenerate}
		{generateDisabledReason}
		onGenerate={startGeneration}
		onCancel={cancelGeneration}
		onOpenPrompt={() => (openSheet = 'prompt')}
		onOpenSettings={() => (openSheet = 'settings')}
		{onMoveToWorkbench}
	/>
</div>

{#if openSheet === 'prompt'}
	<StudioPromptSheet
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
		onClose={closeSheet}
	/>
{:else if openSheet === 'settings'}
	<StudioSettingsSheet
		{tab}
		{presetName}
		{modeLabel}
		{videoDirectorActive}
		{onFormDataChange}
		{dynamicFormRefs}
		onClose={closeSheet}
	/>
{:else if openSheet === 'preset'}
	<StudioPresetSessionSheet
		{tab}
		{presets}
		{readiness}
		{isLoading}
		isReloading={isReloadingPreset}
		{availableModes}
		onPresetChange={tabHandlers.handlePresetChange}
		onModeChange={tabHandlers.handleModeChange}
		onVariantChange={tabHandlers.handleVariantChange}
		{onReload}
		onClose={closeSheet}
	/>
{:else if openSheet === 'chat'}
	<StudioChatSheet onClose={closeSheet} />
{/if}
