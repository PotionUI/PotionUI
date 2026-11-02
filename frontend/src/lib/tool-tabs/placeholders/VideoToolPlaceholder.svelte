<script lang="ts">
	import ToolPlaceholder from './ToolPlaceholder.svelte';
	import type { ToolTabHost } from '../types';

	let { host }: { host: ToolTabHost } = $props();
</script>

<ToolPlaceholder
	{host}
	media="video"
	icon="upload"
	emptyTitle="Open a clip to edit"
	emptyText="Open a video from History or Library. Every edit stays undoable until you save."
/>
