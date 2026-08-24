<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import { PageHeader, IconButton, Spinner, Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { listModelViewSections } from '$lib/registries/modelViewRegistry';
	import ModelDetailsHeader from '$lib/components/modals/model-details/ModelDetailsHeader.svelte';
	import ModelDetailsContent from '$lib/components/modals/model-details/ModelDetailsContent.svelte';
	import { createLibraryModelDetailsController } from '$lib/components/modals/model-details/modelDetailsController';

	// User-facing model page: no file size, hash, path or indexing timestamp, whoever
	// is looking. `createLibraryModelDetailsController` cannot hold or request any of
	// that. Admins get those in Admin -> Models. See docs/models.md.

	// Store the referrer URL to go back to
	let backUrl = '/models';

	function goBack() {
		goto(backUrl);
	}

	const controller = createLibraryModelDetailsController();
	const {
		capabilities,
		model,
		loading,
		currentImageIndex,
		imageFiles,
		displayName,
		selectedTags,
		isFavorite
	} = controller;

	$: collections = $modelLibraryStore.collections;

	// SvelteKit types params as possibly-undefined; the loader below no-ops without one
	// rather than requesting `/api/models/undefined`.
	$: modelId = $page.params.model_id ?? '';

	function handleRename(event: CustomEvent<string>) {
		controller.rename(event.detail);
	}

	onMount(async () => {
		// Get the stored models list URL for back navigation
		if (browser) {
			const storedUrl = sessionStorage.getItem('modelsListUrl');
			if (storedUrl) {
				try {
					const url = new URL(storedUrl);
					backUrl = url.pathname + url.search;
				} catch (e) {
					// Invalid URL, use default
				}
			}
		}
		if (collections.length === 0) modelLibraryStore.load();
		if (modelId) await controller.load(modelId);
	});
</script>

<div class="min-h-screen bg-canvas">
	<!-- Top Bar -->
	<PageHeader sticky={false}>
		<div class="flex items-center gap-4 w-full min-w-0">
			<IconButton icon="chevron-left" label="Back to models" onclick={goBack} />

			<div class="h-6 w-px bg-line-strong"></div>

			<Icon name="model" className="w-5 h-5 text-fg-muted flex-shrink-0" />

			{#if $model}
				<ModelDetailsHeader
					modelType={$model.model_type}
					displayName={$displayName}
					customName={$model.custom_name}
					isFavorite={$isFavorite}
					{collections}
					onAddToCollection={controller.addToCollection}
					on:rename={handleRename}
					on:toggleFavorite={controller.toggleFavorite}
				/>
			{:else}
				<span class="text-sm font-semibold text-fg">Model Details</span>
			{/if}
		</div>
	</PageHeader>

	<div class="px-6 py-6 space-y-10">
		{#if $loading}
			<div class="flex items-center justify-center py-20">
				<Spinner size="lg" />
			</div>
		{:else if $model}
			<ModelDetailsContent
				{capabilities}
				variant="page"
				model={$model}
				currentImageIndex={$currentImageIndex}
				imageFiles={$imageFiles}
				displayName={$displayName}
				selectedTags={$selectedTags}
				onPrevImage={controller.prevImage}
				onNextImage={controller.nextImage}
			/>

			<!-- Plugin model.view sections (A5 extension) -->
			{#each listModelViewSections() as section (section.pluginId + ':' + section.key)}
				{#await section.component then Component}
					{#if Component}
						<div class="bg-surface-1 border border-line rounded-lg shadow-raised p-6">
							<svelte:component this={Component} model={$model} />
						</div>
					{/if}
				{/await}
			{/each}
		{:else}
			<div class="text-center py-20">
				<p class="text-fg-muted text-lg">Model not found</p>
				<Button variant="secondary" class="mt-4" onclick={goBack}>Back to Models</Button>
			</div>
		{/if}
	</div>
</div>
