<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
</script>

<Tooltip text="Delete the shot" delay={0}>
	<IconButton icon="trash" label="Delete" tooltip="Delete it now" kbd="Del" />
</Tooltip>
