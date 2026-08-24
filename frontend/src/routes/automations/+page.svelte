<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	// Automations now live inside the Admin section as a tab.
	onMount(() => {
		goto('/admin?tab=automations', { replaceState: true });
	});
</script>

<svelte:head>
	<title>Automations · PotionUI</title>
</svelte:head>
