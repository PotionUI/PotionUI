<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { api } from '$lib/services/api/index';
	import { presetAltText } from '$lib/utils/presetMedia';
	import { presetCategoryIcon } from '$lib/utils/presetCategories';

	let {
		presetId,
		presetName,
		cover = null,
		category = null,
		class: className = ''
	}: {
		presetId: string;
		presetName: string;
		cover?: string | null;
		category?: string | null;
		class?: string;
	} = $props();

	let errored = $state(false);

	$effect(() => {
		void cover;
		errored = false;
	});

	const showImage = $derived(!!cover && !errored);
	const iconName = $derived(presetCategoryIcon(category));
	const imageUrl = $derived(
		cover && (/^(https?:)?\/\//.test(cover) || cover.startsWith('/'))
			? cover
			: cover
				? api.getPresetAssetURL(presetId, cover, 'small')
				: ''
	);
</script>

<div
	class="relative flex-shrink-0 overflow-hidden bg-gradient-to-br from-surface-3 via-surface-2 to-surface-3 {className}"
>
	{#if showImage}
		<img
			src={imageUrl}
			alt={presetAltText(presetName)}
			class="h-full w-full object-cover"
			loading="lazy"
			onerror={() => (errored = true)}
		/>
	{:else}
		<div class="flex h-full w-full items-center justify-center text-fg-disabled" aria-hidden="true">
			<Icon name={iconName} className="h-1/2 w-1/2 max-h-6 max-w-6" strokeWidth={1.6} />
		</div>
	{/if}
</div>
