<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import PromptWorkspace from './components/PromptWorkspace.svelte';
	import SegmentsWorkspace from './sections/SegmentsWorkspace.svelte';
	import TemplatesWorkspace from './sections/TemplatesWorkspace.svelte';
	import CategoriesWorkspace from './sections/CategoriesWorkspace.svelte';
	import { primeLibraryCounts } from './library/libraryCounts';
	import { sectionFromSearchParams } from './library/librarySection';

	$: section = sectionFromSearchParams($page.url.searchParams);

	onMount(() => {
		void primeLibraryCounts();
	});
</script>

<svelte:head><title>Prompt Library</title></svelte:head>

{#if section === 'segments'}
	<SegmentsWorkspace />
{:else if section === 'templates'}
	<TemplatesWorkspace />
{:else if section === 'categories'}
	<CategoriesWorkspace />
{:else}
	<PromptWorkspace />
{/if}
