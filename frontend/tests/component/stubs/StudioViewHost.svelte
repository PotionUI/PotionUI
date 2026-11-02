<script lang="ts">
	import { generationTabs } from '$lib/types/tabs';
	import { tabsStore } from '$lib/stores/tabs';
	import { formDataPublicationPatch } from '$lib/utils/sessionTabState';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import StudioView from '../../../src/routes/generate/components/studio/StudioView.svelte';

	let { tabId }: { tabId: string } = $props();

	const tabs = $derived(generationTabs($tabsStore.tabs).filter((candidate) => candidate.id === tabId));
	const dynamicFormRefs: Record<string, DynamicForm> = {};
	const tabHandlers = {
		handlePresetChange: () => {},
		handleModeChange: () => {},
		handleVariantChange: () => {},
		handlePromptChange: () => {},
		handlePromptSegmentsChange: () => {},
		handleNegativePromptChange: () => {},
		handleNegativePromptSegmentsChange: () => {}
	};

	function handleFormDataChange(data: Record<string, unknown>) {
		const current = generationTabs($tabsStore.tabs).find((candidate) => candidate.id === tabId);
		if (!current) return;
		tabsStore.updateTab(tabId, formDataPublicationPatch(current, data));
	}
</script>

{#each tabs as tab (tab.id)}
	<StudioView
		{tab}
		{tabHandlers}
		presets={[]}
		readiness={null}
		isLoading={false}
		isReloadingPreset={false}
		tabModes={[]}
		promptRelayActive={false}
		videoDirectorActive={false}
		videoDirectorCaps={null}
		musicDirectorActive={false}
		musicDirectorCaps={null}
		canBatch={false}
		negativePromptSupported={false}
		negativeInert={false}
		promptlessActive={false}
		{dynamicFormRefs}
		onFormDataChange={handleFormDataChange}
		onReload={() => {}}
		onMoveToWorkbench={() => {}}
	/>
{/each}
