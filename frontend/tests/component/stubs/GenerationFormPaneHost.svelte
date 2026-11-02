<script lang="ts">
	import { generationTabs } from '$lib/types/tabs';
	import { tabsStore } from '$lib/stores/tabs';
	import { formDataPublicationPatch } from '$lib/utils/sessionTabState';
	import GenerationFormPane from '../../../src/routes/generate/components/GenerationFormPane.svelte';

	let { tabId }: { tabId: string } = $props();

	const tabs = $derived(generationTabs($tabsStore.tabs).filter((candidate) => candidate.id === tabId));

	function handleFormDataChange(data: Record<string, unknown>) {
		const current = generationTabs($tabsStore.tabs).find((candidate) => candidate.id === tabId);
		if (!current) return;
		tabsStore.updateTab(tabId, formDataPublicationPatch(current, data));
	}
</script>

{#each tabs as tab (tab.id)}
	<GenerationFormPane {tab} onFormDataChange={handleFormDataChange} />
{/each}
