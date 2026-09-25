<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	onMount(() => {
		goto(`/admin?tab=automations&id=${encodeURIComponent($page.params.id ?? '')}`, { replaceState: true });
	});
</script>

<svelte:head>
	<title>Automation · PotionUI</title>
</svelte:head>
