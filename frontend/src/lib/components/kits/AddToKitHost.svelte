<script lang="ts">
	import { clearKitAddRequest, kitAddRequest } from '$lib/stores/kitAddRequests';
	import KitComposeDialog from './KitComposeDialog.svelte';
</script>

{#if $kitAddRequest}
	<KitComposeDialog picks={$kitAddRequest} onDone={clearKitAddRequest} onClose={clearKitAddRequest} />
{/if}
