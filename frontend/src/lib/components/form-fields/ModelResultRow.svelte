<!--
	One row of a model search/browse result list: thumbnail, name, filename
	stem, summary line, favorite star. The exact-duplicate block an
	audit flagged between ModelField.svelte's dropdown and LoraPickerField.svelte's
	search results - both, plus ModelCollectionBrowser.svelte's row (near-identical,
	just 2px off on gap/padding), now render through this instead. `size`
	preserves each caller's previous dimensions ("md" = ModelField's old dropdown
	size; "sm" = LoraPickerField's and ModelCollectionBrowser's old size).
-->
<script lang="ts">
	import { filesWithPreview } from '$lib/utils/modelPreview';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { modelFilenameStem, modelSummaryParts, modelTypePresentation } from '$lib/utils/modelPresentation';
	import Icon from '../Icon.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let model: any;
	export let size: 'sm' | 'md' = 'md';
	export let onSelect: (model: any) => void;
	export let onToggleFavorite: (model: any, event: Event) => void;
	/** Extra border-left accent for a matched recommendation (ModelField only). */
	export let accented = false;

	$: previewFiles = filesWithPreview(model);
	$: imageFile = previewFiles.find((f: any) => f.file_type === 'image');
	$: fallbackFile = previewFiles.find((f: any) => f.thumbnail_small);
	$: thumbnailUrl = imageFile?.thumbnail_small || fallbackFile?.thumbnail_small;
	$: displayName = modelDisplayName(model);
	$: filenameStem = modelFilenameStem(model);
	$: summaryParts = modelSummaryParts(model);
	$: purpose = modelTypePresentation(model?.model_type).purpose;

	const dims = {
		sm: { thumb: 'w-10 h-10', thumbRounded: 'rounded', icon: 'w-4 h-4', gap: 'gap-2.5', pad: 'p-2' },
		md: { thumb: 'w-12 h-12', thumbRounded: 'rounded-md', icon: 'w-5 h-5', gap: 'gap-3', pad: 'p-2.5' }
	};
	$: d = dims[size];
</script>

<div
	role="button"
	tabindex="0"
	on:click={() => onSelect(model)}
	on:keydown={(e) => e.key === 'Enter' && onSelect(model)}
	class="w-full flex {d.gap} items-center {d.pad} hover:bg-surface-2 cursor-pointer border-b border-line last:border-b-0 text-left transition-colors {accented
		? 'border-l-2 border-l-info'
		: ''}"
>
	{#if thumbnailUrl}
		<img
			src={thumbnailUrl}
			alt={displayName}
			class="shrink-0 {d.thumb} object-cover {d.thumbRounded}"
			loading="lazy"
		/>
	{:else}
		<div
			class="{d.thumb} shrink-0 bg-surface-3 {d.thumbRounded} flex items-center justify-center text-fg-subtle"
			style={placeholderTint(displayName)}
		>
			<Icon name="image" className={d.icon} />
		</div>
	{/if}
	<div class="flex-1 min-w-0">
		<div class="flex items-center gap-1.5 min-w-0">
			<div class="text-sm font-medium text-fg truncate" title={displayName}>
				{displayName}
			</div>
			<slot name="badge" />
		</div>
		{#if filenameStem}
			<div class="truncate font-mono text-2xs text-fg-muted" title={model.filename}>
				{filenameStem}
			</div>
		{/if}
		<div class="mt-0.5 truncate text-xs text-fg-subtle" title={purpose}>
			{summaryParts.join(' · ')}
		</div>
	</div>
	<button
		type="button"
		on:click|stopPropagation={(e) => onToggleFavorite(model, e)}
		class="shrink-0 p-1 hover:bg-surface-3 rounded {model.is_favorite ? 'text-warning' : 'text-fg-subtle'}"
		title={model.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
		aria-label={model.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
	>
		<Icon name="star" className="w-3.5 h-3.5" />
	</button>
</div>
