<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	onMount(() => {
		goto('/admin?tab=docs&doc=live/frontend-kit', { replaceState: true });
	});
</script>

<svelte:head>
	<title>Frontend Kit · PotionUI</title>
</svelte:head>
