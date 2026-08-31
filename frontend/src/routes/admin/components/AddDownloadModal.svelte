<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { createEventDispatcher, onMount } from 'svelte';
	import { downloadStore, remoteBackends, type QueueModelDownloadOptions } from '$lib/stores/downloads';
	import { api } from '$lib/services/api/index';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner, Alert } from '$lib/components/ui';

	interface ApiModelTypeItem {
		type: string;
		directory?: string;
		count?: number;
	}

	interface ApiProviderItem {
		id: string;
		name: string;
	}

	interface ApiTagItem {
		id: string;
		name: string;
	}

	const dispatch = createEventDispatcher();

	let url = '';
	let destinationType = '';
	let filename = '';
	let selectedTags: string[] = [];
	let selectedProviderId = '';
	let destinationBackendId = '';
	let checksumSha256 = '';
	let submitting = false;
	let errorMessage = '';

	// Loaded data
	let modelTypes: { type: string; directory: string; count: number }[] = [];
	let providers: { id: string; name: string }[] = [];
	let availableTags: { id: string; name: string }[] = [];
	let loadingData = true;

	// Tag search
	let tagSearchQuery = '';
	let tagSearchResults: { id: string; name: string }[] = [];
	let showTagDropdown = false;
	let searchingTags = false;

	onMount(async () => {
		await loadInitialData();
	});

	async function loadInitialData() {
		loadingData = true;
		try {
			// Load model types, providers, and tags in parallel
			const [typesRes, providersRes, tagsRes] = await Promise.all([
				api.getModelTypes({ include_empty: true }),
				api.getProviders(),
				api.getTags('MODEL')
			]);

			if (typesRes.success && typesRes.data?.types) {
				modelTypes = (typesRes.data.types as ApiModelTypeItem[]).map((t) => ({
					type: t.type,
					directory: t.directory || `models/${t.type}`,
					count: t.count || 0
				}));
				// Set default selection to checkpoint if available
				if (modelTypes.length > 0) {
					const checkpoint = modelTypes.find(t => t.type === 'checkpoint');
					destinationType = checkpoint?.type || modelTypes[0].type;
				}
			}

			if (providersRes.success && providersRes.data) {
				providers = (providersRes.data as ApiProviderItem[]).map((p) => ({
					id: p.id,
					name: p.name
				}));
			}

			if (tagsRes.success && tagsRes.data?.tags) {
				availableTags = (tagsRes.data.tags as ApiTagItem[]).map((t) => ({
					id: t.id,
					name: t.name
				}));
			}
		} catch (err) {
			logger.error('Failed to load initial data:', err);
		} finally {
			loadingData = false;
		}
	}

	async function searchTags(query: string) {
		if (!query.trim()) {
			tagSearchResults = [];
			return;
		}

		searchingTags = true;
		try {
			const res = await api.searchTags(query, 'MODEL', 10);
			if (res.success && res.data?.tags) {
				// Filter out already selected tags
				tagSearchResults = (res.data.tags as ApiTagItem[])
					.filter((t) => !selectedTags.includes(t.id))
					.map((t) => ({
						id: t.id,
						name: t.name
					}));
			}
		} catch (err) {
			logger.error('Failed to search tags:', err);
		} finally {
			searchingTags = false;
		}
	}

	function handleTagSearchInput(e: Event) {
		const value = (e.target as HTMLInputElement).value;
		tagSearchQuery = value;
		showTagDropdown = true;
		searchTags(value);
	}

	function addTag(tag: { id: string; name: string }) {
		if (!selectedTags.includes(tag.id)) {
			selectedTags = [...selectedTags, tag.id];
			// Add to available tags if not already there
			if (!availableTags.find(t => t.id === tag.id)) {
				availableTags = [...availableTags, tag];
			}
		}
		tagSearchQuery = '';
		tagSearchResults = [];
		showTagDropdown = false;
	}

	function removeTag(tagId: string) {
		selectedTags = selectedTags.filter(id => id !== tagId);
	}

	function getTagName(tagId: string): string {
		return availableTags.find(t => t.id === tagId)?.name || tagId;
	}

	function getSelectedDirectory(): string {
		const selected = modelTypes.find(t => t.type === destinationType);
		return selected?.directory || 'models';
	}

	async function handleSubmit() {
		if (!url.trim()) {
			errorMessage = 'URL is required';
			return;
		}

		submitting = true;
		errorMessage = '';

		try {
			// The server resolves `model_type` against the configured model depot
			// itself (see DownloadManager.queue_model_download) - the directory
			// string shown below is for display only, never sent as the destination.
			const options: QueueModelDownloadOptions = {
				model_type: destinationType
			};
			if (filename.trim()) options.filename = filename.trim();
			// Convert tag IDs to tag names for the download service
			if (selectedTags.length > 0) {
				options.tags = selectedTags.map(id => getTagName(id));
			}
			if (checksumSha256.trim()) options.checksum_sha256 = checksumSha256.trim();
			if (selectedProviderId) options.provider_id = selectedProviderId;
			if (destinationBackendId) options.destination_backend_id = destinationBackendId;

			const result = await downloadStore.queueModelDownload(url.trim(), options);

			if (result) {
				dispatch('close');
			} else {
				errorMessage = 'Failed to queue download';
			}
		} catch (err: unknown) {
			errorMessage = getErrorMessage(err, 'Failed to queue download');
		} finally {
			submitting = false;
		}
	}

	function close() {
		dispatch('close');
	}

	function handleClickOutsideTagDropdown(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.closest('.tag-search-container')) {
			showTagDropdown = false;
		}
	}
</script>

<svelte:window on:click={handleClickOutsideTagDropdown} />

<BaseModal isOpen={true} title="Add Model Download" sizeClass="md:max-w-lg md:w-full" on:close={close}>
	{#if loadingData}
		<div class="px-6 py-12 flex flex-col items-center justify-center">
			<Spinner size="lg" />
			<p class="text-sm text-fg-muted mt-3">Loading...</p>
		</div>
	{:else}
		<form on:submit|preventDefault={handleSubmit} class="px-6 py-4 space-y-4">
			<!-- Error Message -->
			{#if errorMessage}
				<Alert variant="danger" density="compact">{errorMessage}</Alert>
			{/if}

			<!-- URL -->
			<div>
				<label for="url" class="block text-sm font-medium text-fg-muted mb-1">
					Download URL <span class="text-danger">*</span>
				</label>
				<input
					id="url"
					type="url"
					bind:value={url}
					placeholder="https://example.com/model.safetensors"
					class="input"
					required
				/>
			</div>

			<!-- Model Type / Destination -->
			<div>
				<label for="destination" class="block text-sm font-medium text-fg-muted mb-1">
					Model Type
				</label>
				<select
					id="destination"
					bind:value={destinationType}
					class="input"
				>
					{#each modelTypes as modelType}
						<option value={modelType.type}>
							{modelType.type} ({modelType.directory})
						</option>
					{/each}
				</select>
				<p class="text-xs text-fg-subtle mt-1">
					Downloads to: <code class="bg-surface-3 px-1 py-0.5 rounded font-mono">{getSelectedDirectory()}</code>
				</p>
			</div>

			<!-- Provider -->
			{#if providers.length > 0}
				<div>
					<label for="provider" class="block text-sm font-medium text-fg-muted mb-1">
						Provider (optional)
					</label>
					<select
						id="provider"
						bind:value={selectedProviderId}
						class="input"
					>
						<option value="">No provider</option>
						{#each providers as provider}
							<option value={provider.id}>{provider.name}</option>
						{/each}
					</select>
					<p class="text-xs text-fg-subtle mt-1">
						Associate this download with a model provider
					</p>
				</div>
			{/if}

			<!-- Destination -->
			{#if $remoteBackends.length > 0}
				<div>
					<label for="destination-backend" class="block text-sm font-medium text-fg-muted mb-1">
						Destination
					</label>
					<select
						id="destination-backend"
						bind:value={destinationBackendId}
						class="input"
					>
						<option value="">Local</option>
						{#each $remoteBackends as backend}
							<option value={backend.id}>{backend.name}</option>
						{/each}
					</select>
					<p class="text-xs text-fg-subtle mt-1">
						{#if destinationBackendId}
							Downloaded straight onto this worker's depot - never touches this host's disk
						{:else}
							Downloaded to this host's model depot
						{/if}
					</p>
				</div>
			{/if}

			<!-- Filename -->
			<div>
				<label for="filename" class="block text-sm font-medium text-fg-muted mb-1">
					Filename (optional)
				</label>
				<input
					id="filename"
					type="text"
					bind:value={filename}
					placeholder="Auto-detected from URL"
					class="input"
				/>
			</div>

			<!-- Tags -->
			<div class="tag-search-container">
				<label for="tag-search" class="block text-sm font-medium text-fg-muted mb-1">
					Tags
				</label>

				<!-- Selected Tags -->
				{#if selectedTags.length > 0}
					<div class="flex flex-wrap gap-1.5 mb-2">
						{#each selectedTags as tagId}
							<span class="inline-flex items-center gap-1 px-2 py-1 bg-surface-3 rounded-md text-sm text-fg">
								{getTagName(tagId)}
								<button
									type="button"
									class="text-fg-subtle hover:text-fg-muted"
									on:click={() => removeTag(tagId)}
								>
									<Icon name="close" className="w-3.5 h-3.5" />
								</button>
							</span>
						{/each}
					</div>
				{/if}

				<!-- Tag Search Input -->
				<div class="relative">
					<input
						id="tag-search"
						type="text"
						value={tagSearchQuery}
						on:input={handleTagSearchInput}
						on:focus={() => showTagDropdown = true}
						placeholder="Search tags..."
						class="input"
					/>

					<!-- Search Results Dropdown -->
					{#if showTagDropdown && (tagSearchResults.length > 0 || searchingTags)}
						<div class="absolute z-10 w-full mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-48 overflow-y-auto">
							{#if searchingTags}
								<div class="px-3 py-2 text-sm text-fg-subtle">Searching...</div>
							{:else}
								{#each tagSearchResults as tag}
									<button
										type="button"
										class="w-full px-3 py-2 text-left text-sm text-fg hover:bg-surface-3 transition-colors"
										on:click={() => addTag(tag)}
									>
										{tag.name}
									</button>
								{/each}
							{/if}
						</div>
					{/if}
				</div>

				<!-- Quick select from available tags -->
				{#if availableTags.length > 0 && !tagSearchQuery}
					<div class="mt-2">
						<p class="text-xs text-fg-subtle mb-1">Quick add:</p>
						<div class="flex flex-wrap gap-1">
							{#each availableTags.filter(t => !selectedTags.includes(t.id)).slice(0, 8) as tag}
								<button
									type="button"
									class="px-2 py-0.5 text-xs text-fg-muted bg-surface-3 hover:bg-line-hover border border-line rounded transition-colors"
									on:click={() => addTag(tag)}
								>
									+ {tag.name}
								</button>
							{/each}
						</div>
					</div>
				{/if}
			</div>

			<!-- SHA256 Checksum -->
			<div>
				<label for="checksum" class="block text-sm font-medium text-fg-muted mb-1">
					SHA256 Checksum (optional)
				</label>
				<input
					id="checksum"
					type="text"
					bind:value={checksumSha256}
					placeholder="For verification after download"
					class="input font-mono text-sm"
				/>
				<p class="text-xs text-fg-subtle mt-1">
					If provided, the download will be verified after completion
				</p>
			</div>

			<!-- Submit Button -->
			<div class="flex gap-3 pt-2">
				<Button type="submit" variant="primary" class="flex-1" disabled={submitting || !url.trim()}>
					{#if submitting}
						<Spinner size="sm" />
						Queueing...
					{:else}
						<Icon name="download" className="w-4 h-4" />
						Queue Download
					{/if}
				</Button>
				<Button type="button" variant="secondary" onclick={close}>
					Cancel
				</Button>
			</div>
		</form>
	{/if}
</BaseModal>
