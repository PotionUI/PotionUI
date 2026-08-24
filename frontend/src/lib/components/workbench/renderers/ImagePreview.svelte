<script lang="ts">
	/**
	 * `workbench.file` core default for `file_type: "image"`. Thin wrapper
	 * around `ImageViewer` so the file-type -> component registry has a
	 * registrable entry identical to what `Workbench.svelte` rendered inline
	 * before extraction.
	 */
	import ImageViewer from '$lib/components/workbench/ImageViewer.svelte';

	export let imageUrl: string;
	export let comparisonImage: string | null = null;
	export let isComparing: boolean = false;
	export let isZoomMode: boolean = false;
</script>

<ImageViewer
	{imageUrl}
	{comparisonImage}
	{isComparing}
	{isZoomMode}
	on:exitComparison
	on:exitZoom
	on:imageClick
	on:imageDoubleClick
/>
