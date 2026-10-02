<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { fallbackIconForCategory } from '$lib/utils/presetMedia';
	import PresetCoverMedia from './PresetCoverMedia.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let presetId: string;
	export let presetName: string;
	export let cover: string | null | undefined = null;
	export let category: string | null | undefined = null;
	/** Tailwind size classes for the fixed square box, e.g. "w-9 h-9". */
	export let size: string = 'w-9 h-9';
	/** Requested rendition. Large detail artwork should not be stretched from a list thumbnail. */
	export let variant: 'small' | 'medium' | 'large' = 'small';
	export let zoom: boolean = true;

	let errored = false;
	// Reset the error flag when the cover itself changes (e.g. row reused for a different preset).
	$: cover, (errored = false);

	$: showImage = !!cover && !errored;
	$: iconName = fallbackIconForCategory(category);
</script>

<div class="{size} flex-shrink-0 aspect-square rounded overflow-hidden bg-surface-2 border border-line">
	{#if showImage}
		<PresetCoverMedia {presetId} {presetName} cover={cover ?? ''} {variant} {zoom} onfail={() => (errored = true)} />
	{:else}
		<div
			class="w-full h-full flex items-center justify-center text-fg-subtle"
			style={placeholderTint(presetName || presetId)}
			aria-hidden="true"
		>
			<Icon name={iconName} className="w-1/2 h-1/2" strokeWidth={1.5} />
		</div>
	{/if}
</div>
