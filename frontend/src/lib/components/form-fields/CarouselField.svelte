<script lang="ts">
	import { onMount } from 'svelte';
	import { isMobile } from '$lib/stores/viewport';
	import { api } from '$lib/services/api/index';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: options = config.options || [];
	// Carousel's own configuration block (preset_id lives on the outer field
	// config; everything else comes from `configuration`).
	$: carouselConfig = { preset_id: config.preset_id, ...config.configuration };

	// Extract configuration
	$: presetId = carouselConfig?.preset_id || '';
	$: multiSelect = carouselConfig?.multi_select || false;
	$: rows = carouselConfig?.rows || 2;
	$: columns = carouselConfig?.columns || 3;
	$: itemWidth = carouselConfig?.item_width || 150;
	$: itemHeight = carouselConfig?.item_height || 150;
	$: mode = carouselConfig?.mode || 'grid';
	$: showLabels = carouselConfig?.show_labels !== false;

	// On mobile, use 2 columns with auto-sized items that fit the viewport
	$: mobileColumns = Math.min(columns, 2);

	// Track selected items
	let selectedItems: Set<string> = new Set();
	let imageLoadErrors: Set<string> = new Set();

	// Initialize selected items from value
	$: {
		if (multiSelect && Array.isArray(value)) {
			selectedItems = new Set(value);
		} else if (value && !multiSelect) {
			selectedItems = new Set([value]);
		} else {
			selectedItems = new Set();
		}
	}

	function handleItemClick(itemValue: string) {
		if (multiSelect) {
			// Toggle selection for multi-select
			const newSelected = new Set(selectedItems);
			if (newSelected.has(itemValue)) {
				newSelected.delete(itemValue);
			} else {
				newSelected.add(itemValue);
			}
			selectedItems = newSelected;
			if (name) {
				onChange(name, Array.from(selectedItems));
			}
		} else {
			// Single selection
			if (selectedItems.has(itemValue)) {
				// Deselect if clicking the same item
				selectedItems = new Set();
				if (name) {
					onChange(name, null);
				}
			} else {
				selectedItems = new Set([itemValue]);
				if (name) {
					onChange(name, itemValue);
				}
			}
		}
	}

	function getImageUrl(imagePath: string): string {
		if (!imagePath) return '';
		return api.getPresetAssetURL(presetId, imagePath);
	}

	function handleImageError(itemValue: string) {
		imageLoadErrors = new Set([...imageLoadErrors, itemValue]);
	}

	function isSelected(itemValue: string): boolean {
		return selectedItems.has(itemValue);
	}
</script>

<div class="field-card">
	<label class="label" id={name ? `${name}-label` : undefined}>
		{label}
	</label>

	{#if options.length === 0}
		<p class="text-sm text-fg-muted italic">No items available</p>
	{:else if mode === 'grid'}
		<!-- Grid Layout -->
		<div
			class="carousel-grid mt-2 gap-2 md:gap-3 overflow-x-auto"
			style="display: grid; grid-template-columns: {$isMobile ? `repeat(${mobileColumns}, 1fr)` : `repeat(${columns}, ${itemWidth}px)`}; grid-auto-rows: {($isMobile ? Math.min(itemHeight, 120) : itemHeight) +
				(showLabels ? 30 : 0)}px;"
		>
			{#each options as item (item.value)}
				<div
					class="carousel-item relative cursor-pointer rounded-lg border-2 transition-all hover:opacity-80"
					class:selected={isSelected(item.value)}
					class:border-signal={isSelected(item.value)}
					class:border-line-strong={!isSelected(item.value)}
					on:click={() => handleItemClick(item.value)}
					on:keydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') {
							e.preventDefault();
							handleItemClick(item.value);
						}
					}}
					role="button"
					tabindex="0"
					title={item.description || item.label}
					style="height: {$isMobile ? Math.min(itemHeight, 120) : itemHeight}px;"
				>
					<!-- Image -->
					<div
						class="w-full h-full flex items-center justify-center bg-surface-2 rounded-lg overflow-hidden"
					>
						{#if item.image && !imageLoadErrors.has(item.value)}
							<img
								src={getImageUrl(item.image)}
								alt={item.label}
								class="w-full h-full object-cover"
								loading="lazy"
								on:error={() => handleImageError(item.value)}
							/>
						{:else}
							<!-- Placeholder for missing/error images -->
							<div class="flex flex-col items-center justify-center text-fg-subtle">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									class="h-12 w-12"
									fill="none"
									viewBox="0 0 24 24"
									stroke="currentColor"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
									/>
								</svg>
								<span class="text-xs mt-1">No Image</span>
							</div>
						{/if}
					</div>

					<!-- Selection Indicator -->
					{#if isSelected(item.value)}
						<div
							class="absolute top-1 right-1 bg-signal-solid text-white rounded-full w-5 h-5 flex items-center justify-center"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								class="h-3 w-3"
								viewBox="0 0 20 20"
								fill="currentColor"
							>
								<path
									fill-rule="evenodd"
									d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
					{/if}

					<!-- Label -->
					{#if showLabels}
						<div class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent text-white px-1.5 pt-3 pb-1">
							<p class="text-xs text-center truncate">{item.label}</p>
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{:else}
		<!-- Carousel Layout (simplified - can be enhanced with prev/next buttons) -->
		<div class="carousel-container mt-2 overflow-x-auto">
			<div class="flex gap-3" style="min-width: min-content;">
				{#each options as item (item.value)}
					<div
						class="carousel-item relative cursor-pointer rounded-lg border-2 transition-all hover:opacity-80 flex-shrink-0"
						class:selected={isSelected(item.value)}
						class:border-signal={isSelected(item.value)}
						class:ring-2={isSelected(item.value)}
						class:ring-signal={isSelected(item.value)}
						class:border-line-strong={!isSelected(item.value)}
						on:click={() => handleItemClick(item.value)}
						on:keydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								handleItemClick(item.value);
							}
						}}
						role="button"
						tabindex="0"
						title={item.description || item.label}
						style="width: {itemWidth}px; height: {itemHeight}px;"
					>
						<!-- Image -->
						<div
							class="w-full h-full flex items-center justify-center bg-surface-2 rounded-lg overflow-hidden"
						>
							{#if item.image && !imageLoadErrors.has(item.value)}
								<img
									src={getImageUrl(item.image)}
									alt={item.label}
									class="w-full h-full object-cover"
									loading="lazy"
									on:error={() => handleImageError(item.value)}
								/>
							{:else}
								<!-- Placeholder -->
								<div class="flex flex-col items-center justify-center text-fg-subtle">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										class="h-12 w-12"
										fill="none"
										viewBox="0 0 24 24"
										stroke="currentColor"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
										/>
									</svg>
									<span class="text-xs mt-1">No Image</span>
								</div>
							{/if}
						</div>

						<!-- Selection Indicator -->
						{#if isSelected(item.value)}
							<div
								class="absolute top-1 right-1 bg-signal-solid text-white rounded-full w-6 h-6 flex items-center justify-center"
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									class="h-4 w-4"
									viewBox="0 0 20 20"
									fill="currentColor"
								>
									<path
										fill-rule="evenodd"
										d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
						{/if}

						<!-- Label -->
						{#if showLabels}
							<div class="absolute bottom-0 left-0 right-0 bg-black bg-opacity-60 text-white p-1">
								<p class="text-xs text-center truncate">{item.label}</p>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	{/if}

	{#if description}
		<p class="text-xs text-fg-muted mt-2">{description}</p>
	{/if}

	{#if multiSelect && selectedItems.size > 0}
		<p class="text-xs text-fg-muted mt-2">
			{selectedItems.size} item{selectedItems.size !== 1 ? 's' : ''} selected
		</p>
	{/if}
</div>

<style>
	.carousel-grid {
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-hover) / 0.5) transparent;
	}

	.carousel-grid::-webkit-scrollbar {
		height: 8px;
	}

	.carousel-grid::-webkit-scrollbar-track {
		background: transparent;
	}

	.carousel-grid::-webkit-scrollbar-thumb {
		background-color: rgb(var(--line-hover) / 0.5);
		border-radius: 4px;
	}

	.carousel-container {
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-hover) / 0.5) transparent;
	}

	.carousel-container::-webkit-scrollbar {
		height: 8px;
	}

	.carousel-container::-webkit-scrollbar-track {
		background: transparent;
	}

	.carousel-container::-webkit-scrollbar-thumb {
		background-color: rgb(var(--line-hover) / 0.5);
		border-radius: 4px;
	}

	.carousel-item {
		position: relative;
		user-select: none;
	}

	.carousel-item.selected {
		box-shadow: 0 0 0 2px rgb(var(--signal) / 0.5);
	}
</style>
