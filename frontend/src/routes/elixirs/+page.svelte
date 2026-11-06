<script lang="ts">
	import { onMount } from 'svelte';
	import { Spinner } from '$lib/components/ui';
	import { openLastElixir } from '$lib/elixirs/tab';

	onMount(() => {
		openLastElixir({ replace: true });
	});
</script>

<svelte:head>
	<title>Elixirs · PotionUI</title>
</svelte:head>

<div class="flex h-full items-center justify-center" role="status" aria-label="Opening elixirs">
	<Spinner />
</div>
