<script lang="ts">
	import Workbench from '$lib/components/Workbench.svelte';
	import PluginOutputs from '$lib/components/generation/PluginOutputs.svelte';
	import CancelNotice from '$lib/components/generation/CancelNotice.svelte';
	import LiveTextArtifacts from '$lib/components/generation/LiveTextArtifacts.svelte';
	import LiveModelFileArtifacts from '$lib/components/generation/LiveModelFileArtifacts.svelte';
	import GridWorkbench from '$lib/components/compare/view/GridWorkbench.svelte';
	import { compareRevision, readWorkbenchGrid } from '$lib/generation/compare/compareStore.svelte';
	import { galleryTotal, type WorkbenchBatches } from '$lib/components/workbench/workbenchGallery';
	import type { GenerationTab } from '$lib/types/tabs';
	import { WORKBENCH_PANE_PADDING, type WorkbenchEmptyLayout } from '$lib/components/workbench/workbenchEmpty';

	// The workbench, wired the same way at every mount site (mobile Panel 2,
	// desktop GenerationPanels three-pane and two-pane layouts). Callers keep
	// their own `{#if isActive}` guard around this - that gating differs by
	// layout and stays where it already is.
	export let tab: GenerationTab;
	export let emptyLayout: WorkbenchEmptyLayout;
	export let onWorkbenchPrevious: () => void;
	export let onWorkbenchNext: () => void;
	export let onWorkbenchHeightChange: (event: CustomEvent<string>) => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;

	$: generation = tab.generation;
	$: current = generation.currentGeneration as any;
	let noticeHeight = 0;
	$: stageTop = noticeHeight > 0 ? noticeHeight + WORKBENCH_PANE_PADDING : 0;
	$: hasModelFiles = (generation.artifacts ?? []).some(
		(artifact) => artifact.artifact_type === 'model_file' && !!artifact.artifact_data
	);
	$: hasMedia =
		galleryTotal({
			images: generation.batchImages,
			videos: generation.batchVideos,
			audios: generation.batchAudios,
			meshes: generation.batchMeshes
		} as WorkbenchBatches) > 0 ||
		!!(current?.current_image || current?.current_video || current?.current_audio || current?.preview_clip);
</script>

<div class="flow-root" data-testid="cancel-notice-slot" bind:clientHeight={noticeHeight}>
	<CancelNotice {tab} />
</div>

{#if readWorkbenchGrid($compareRevision, tab.id)}
	<GridWorkbench tabId={tab.id} />
{:else if !hasModelFiles || hasMedia}
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
	{emptyLayout}
	{stageTop}
	samplingProgress={generation.currentProgress?.progress ?? 0}
	samplingStep={(generation.currentProgress as { current_step_num?: number } | null)?.current_step_num ?? 0}
	on:previous={onWorkbenchPrevious}
	on:next={onWorkbenchNext}
	on:heightChange={onWorkbenchHeightChange}
	on:moveToWorkbench={onMoveToWorkbench}
/>
{/if}

<LiveTextArtifacts {tab} />

<LiveModelFileArtifacts {tab} />

<PluginOutputs {tab} />
