<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import PresetExampleCard from './PresetExampleCard.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { processMarkdown } from '$lib/utils/markdown';
	import { fallbackIconForCategory, presetAltText } from '$lib/utils/presetMedia';
	import type { PresetGalleryItem, PresetInfo } from '$lib/types/api';

	export let isOpen: boolean = false;
	export let preset: PresetInfo | null = null;

	const dispatch = createEventDispatcher<{ close: void }>();

	let gallery: PresetGalleryItem[] = [];
	let isLoadingGallery = false;
	let loadedForId: string | null = null;

	// Lazily fetch the detail endpoint (which carries the gallery) whenever the
	// modal opens for a new preset — the list payload only carries the cover.
	$: if (isOpen && preset && loadedForId !== preset.id) {
		loadGallery(preset.id);
	}

	async function loadGallery(presetId: string) {
		isLoadingGallery = true;
		gallery = [];
		try {
			const response = await api.getPreset(presetId);
			if (response.success && response.data) {
				gallery = response.data.media?.gallery ?? [];
			}
			loadedForId = presetId;
		} catch (err) {
			logger.error('Failed to load preset gallery:', err);
			loadedForId = presetId;
		} finally {
			isLoadingGallery = false;
		}
	}

	function handleClose() {
		dispatch('close');
	}

	$: coverUrl = preset?.media?.cover
		? api.getPresetAssetURL(preset.id, preset.media.cover, 'large')
		: null;
	$: fallbackIcon = fallbackIconForCategory(preset?.category);
	$: descriptionHtml = preset?.description ? processMarkdown(preset.description) : '';
</script>

<BaseModal {isOpen} size="xl" closeable on:close={handleClose}>
	<svelte:fragment slot="header">
		<div class="min-w-0">
			<h2 class="text-lg font-semibold text-fg truncate">{preset?.name ?? ''}</h2>
			{#if preset?.version}
				<span class="text-xs text-fg-subtle font-mono tabular-nums">v{preset.version}</span>
			{/if}
		</div>
	</svelte:fragment>

	{#if preset}
		<!-- Cover banner -->
		<div class="w-full h-48 bg-surface-2 border-b border-line">
			{#if coverUrl}
				<img src={coverUrl} alt={presetAltText(preset.name)} class="w-full h-full object-cover" loading="lazy" />
			{:else}
				<div class="w-full h-full flex items-center justify-center text-fg-subtle" aria-hidden="true">
					<Icon name={fallbackIcon} className="w-12 h-12" strokeWidth={1.5} />
				</div>
			{/if}
		</div>

		<div class="p-6 space-y-6">
			{#if descriptionHtml}
				<div class="text-sm text-fg-muted leading-relaxed">
					{@html descriptionHtml}
				</div>
			{/if}

			<div>
				<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted mb-3">Examples</h3>
				{#if isLoadingGallery}
					<div class="flex items-center justify-center py-8">
						<Spinner size="md" />
					</div>
				{:else if gallery.length === 0}
					<p class="text-sm text-fg-subtle italic">No examples yet.</p>
				{:else}
					<div class="grid grid-cols-2 md:grid-cols-3 gap-3">
						{#each gallery as item}
							<PresetExampleCard presetId={preset.id} presetName={preset.name} {item} />
						{/each}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</BaseModal>
