<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { formAudienceStore } from '$lib/stores/formAudience';
	import { createFormStateSeed } from '$lib/form/formStateSeed';
	import { loadCloudCapabilities, loadPresetFormSchema, searchAutopickSlot } from '$lib/form/formSources';
	import { sharedCapabilityCache } from '$lib/form/capabilityTracker';
	import { clearMissingModel, publishMissingModel } from '$lib/generation/requiredModelField';
	import type { Tab } from '$lib/types/tabs';

	let {
		tab,
		videoDirectorActive = false,
		onFormDataChange
	}: {
		tab: Tab;
		videoDirectorActive?: boolean;
		onFormDataChange: (data: Record<string, unknown>) => void;
	} = $props();

	const seed = createFormStateSeed({
		loadSchema: (presetId, mode, variant) => loadPresetFormSchema(presetId, mode, variant),
		search: searchAutopickSlot,
		loadCapabilities: loadCloudCapabilities,
		capabilityOf: (modelId) => sharedCapabilityCache.get(modelId),
		publish: (data) => onFormDataChange(data),
		publishMissing: publishMissingModel,
		clearMissing: clearMissingModel
	});

	const tabId = $derived(tab.id);
	const presetId = $derived(tab.selectedPreset);
	const mode = $derived(tab.selectedMode);
	const variant = $derived(tab.selectedVariant);
	const formData = $derived(tab.formData);
	const formPublished = $derived(!!tab.formPublished);

	$effect(() => {
		const target = {
			tabId,
			presetId,
			mode,
			variant,
			formData,
			formPublished,
			audience: $formAudienceStore,
			videoDirectorActive
		};
		untrack(() => seed.sync(target));
	});

	onDestroy(() => seed.dispose());
</script>
