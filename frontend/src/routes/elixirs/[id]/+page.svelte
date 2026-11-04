<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { Spinner } from '$lib/components/ui';
	import { openElixirTab } from '$lib/elixirs/tab';

	onMount(() => {
		const id = $page.params.id ?? '';
		if (id) openElixirTab({ id }, { replace: true });
	});
</script>

<svelte:head>
	<title>Elixir · PotionUI</title>
</svelte:head>

<div class="flex h-full items-center justify-center" role="status" aria-label="Opening the elixir">
	<Spinner />
</div>
