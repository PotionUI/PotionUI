<script lang="ts">
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { loopVideos } from '$lib/stores/loopVideos';

	let { class: className = '' }: { class?: string } = $props();
</script>

<div class={className} data-loop-toggle>
	<Tooltip text={$loopVideos ? 'Loop on' : 'Loop off'} position="bottom">
		<IconButton
			icon="refresh"
			label="Loop"
			size="sm"
			variant="overlay"
			active={$loopVideos}
			ariaPressed={$loopVideos}
			onclick={() => loopVideos.toggle()}
		/>
	</Tooltip>
</div>
