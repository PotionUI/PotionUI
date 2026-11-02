<script lang="ts">
	import ToolPlaceholder from './ToolPlaceholder.svelte';
	import type { ToolTabHost } from '../types';

	let { host }: { host: ToolTabHost } = $props();
</script>

<ToolPlaceholder
	{host}
	media="image"
	icon="upload"
	emptyTitle="Open a picture to edit"
	emptyText="Open a picture from History or Library. Every edit stays undoable until you save."
/>
