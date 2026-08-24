<script lang="ts">
	import Workbench from '$lib/components/Workbench.svelte';
	import type { Tab } from '$lib/types/tabs';

	// The workbench, wired the same way at every mount site (mobile Panel 2,
	// desktop GenerationPanels three-pane and two-pane layouts). Callers keep
	// their own `{#if isActive}` guard around this - that gating differs by
	// layout and stays where it already is.
	export let tab: Tab;
	export let onWorkbenchPrevious: () => void;
	export let onWorkbenchNext: () => void;
	export let onWorkbenchHeightChange: (event: CustomEvent<string>) => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;
</script>

<Workbench
	currentGeneration={tab.generation.currentGeneration}
	isGenerating={tab.generation.isGenerating}
	workbenchIndex={tab.generation.workbenchIndex}
	workbenchTotal={tab.generation.workbenchTotal}
	batchImages={tab.generation.batchImages}
	batchVideos={tab.generation.batchVideos}
	batchAudios={tab.generation.batchAudios}
	batchMeshes={tab.generation.batchMeshes}
	workbenchMaxHeight={tab.workbenchMaxHeight || '600'}
	on:previous={onWorkbenchPrevious}
	on:next={onWorkbenchNext}
	on:heightChange={onWorkbenchHeightChange}
	on:moveToWorkbench={onMoveToWorkbench}
/>
