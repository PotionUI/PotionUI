<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { createEventDispatcher, onMount } from 'svelte';
	import { downloadStore, remoteBackends, type QueueModelDownloadOptions } from '$lib/stores/downloads';
	import { api } from '$lib/services/api/index';
	import { detectUrl } from '$lib/utils/downloadUrlDetect';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner, Alert, SegmentedControl } from '$lib/components/ui';

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
	let advancedOpen = false;

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

	$: detection = detectUrl(url, providers);
	$: destinationItems = [
		{ id: '', label: 'This machine', icon: 'monitor' },
		...$remoteBackends.map((b) => ({ id: b.id, label: b.name, icon: 'server' }))
	];
	$: useSegmentedDestination = destinationItems.length <= 4;

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
					const checkpoint = modelTypes.find((t) => t.type === 'checkpoint');
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
			if (!availableTags.find((t) => t.id === tag.id)) {
				availableTags = [...availableTags, tag];
			}
		}
		tagSearchQuery = '';
		tagSearchResults = [];
		showTagDropdown = false;
	}

	function removeTag(tagId: string) {
		selectedTags = selectedTags.filter((id) => id !== tagId);
	}

	function getTagName(tagId: string): string {
		return availableTags.find((t) => t.id === tagId)?.name || tagId;
	}

	function getSelectedDirectory(): string {
		const selected = modelTypes.find((t) => t.type === destinationType);
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
				options.tags = selectedTags.map((id) => getTagName(id));
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

<svelte:window onclick={handleClickOutsideTagDropdown} />

<BaseModal isOpen={true} title="Add download" sizeClass="md:max-w-lg md:w-full" on:close={close}>
	<svelte:fragment slot="headerIcon">
		<Icon name="download" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
	</svelte:fragment>

	{#if loadingData}
		<div class="px-6 py-12 flex flex-col items-center justify-center">
			<Spinner size="lg" />
			<p class="text-sm text-fg-muted mt-3">Loading...</p>
		</div>
	{:else}
		<form
			onsubmit={(e) => {
				e.preventDefault();
				handleSubmit();
			}}
			class="px-6 py-4 space-y-4"
		>
			{#if errorMessage}
				<Alert variant="danger" density="compact">{errorMessage}</Alert>
			{/if}

			<!-- URL -->
			<div>
				<label for="url" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
					Source URL <span class="text-danger">*</span>
				</label>
				<input
					id="url"
					type="url"
					bind:value={url}
					placeholder="https://example.com/model.safetensors"
					class="input font-mono text-sm h-11"
					required
				/>
			</div>

			<!-- Detected strip -->
			{#if detection.hostname}
				<div class="flex items-center gap-2.5 flex-wrap bg-signal/6 border border-signal/20 rounded px-3 py-2.5">
					<Icon name="check-circle" className="w-4 h-4 text-signal flex-shrink-0" />
					{#if detection.provider}
						<span class="flex items-center gap-1.5 flex-shrink-0">
							<span class="w-[18px] h-[18px] rounded bg-surface-3 border border-line-strong flex items-center justify-center text-fg-muted">
								<Icon name="globe" className="w-2.5 h-2.5" />
							</span>
							<span class="text-xs font-semibold text-fg">{detection.provider.name}</span>
						</span>
						<span class="text-line-hover flex-shrink-0">·</span>
					{/if}
					{#if detection.filename}
						<span class="flex-1 min-w-0 truncate font-mono text-xs text-fg-muted" title={detection.filename}>
							{detection.filename}
						</span>
					{/if}
					{#if destinationType}
						<span class="flex-shrink-0 font-mono text-2xs uppercase tracking-wide font-semibold text-signal bg-signal/14 px-1.5 py-0.5 rounded">
							{destinationType}
						</span>
					{/if}
				</div>
			{/if}

			<div class="grid grid-cols-2 gap-4">
				{#if $remoteBackends.length > 0}
					<div>
						<span class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">Destination</span>
						{#if useSegmentedDestination}
							<SegmentedControl
								items={destinationItems}
								selected={destinationBackendId}
								onSelect={(id) => (destinationBackendId = id)}
								ariaLabel="Destination"
							/>
						{:else}
							<select bind:value={destinationBackendId} class="input">
								<option value="">Local</option>
								{#each $remoteBackends as backend}
									<option value={backend.id}>{backend.name}</option>
								{/each}
							</select>
						{/if}
					</div>
				{/if}

				<div class={$remoteBackends.length > 0 ? '' : 'col-span-2'}>
					<label for="destination" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
						Model type
					</label>
					<select id="destination" bind:value={destinationType} class="input">
						{#each modelTypes as modelType}
							<option value={modelType.type}>{modelType.type}</option>
						{/each}
					</select>
					<p class="text-xs text-fg-subtle mt-1">
						Downloads to <code class="bg-surface-3 px-1 py-0.5 rounded font-mono">{getSelectedDirectory()}</code>
					</p>
				</div>
			</div>

			<!-- Advanced -->
			<div>
				<button
					type="button"
					class="flex items-center gap-1.5 font-mono text-xs font-semibold uppercase tracking-[0.05em] text-fg-muted hover:text-fg"
					aria-expanded={advancedOpen}
					onclick={() => (advancedOpen = !advancedOpen)}
				>
					<Icon name="chevron-right" className="w-3 h-3 transition-transform {advancedOpen ? 'rotate-90' : ''}" />
					Advanced
				</button>

				{#if advancedOpen}
					<div class="flex flex-col gap-4 mt-2.5 p-3.5 bg-surface-2/50 border border-line rounded">
						<!-- Filename -->
						<div>
							<label for="filename" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
								Filename override
							</label>
							<input
								id="filename"
								type="text"
								bind:value={filename}
								placeholder={detection.filename ? `${detection.filename} (auto)` : 'Auto-detected from URL'}
								class="input font-mono text-sm"
							/>
						</div>

						<!-- Provider -->
						{#if providers.length > 0}
							<div>
								<label for="provider" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
									Provider
								</label>
								<select id="provider" bind:value={selectedProviderId} class="input">
									<option value="">No provider</option>
									{#each providers as provider}
										<option value={provider.id}>
											{provider.name}{detection.provider?.id === provider.id ? ' (detected)' : ''}
										</option>
									{/each}
								</select>
							</div>
						{/if}

						<!-- Tags -->
						<div class="tag-search-container">
							<label for="tag-search" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
								Tags
							</label>

							{#if selectedTags.length > 0}
								<div class="flex flex-wrap gap-1.5 mb-2">
									{#each selectedTags as tagId}
										<span class="inline-flex items-center gap-1 px-2 py-1 bg-surface-3 rounded text-sm text-fg">
											{getTagName(tagId)}
											<button type="button" class="text-fg-subtle hover:text-fg-muted" onclick={() => removeTag(tagId)}>
												<Icon name="close" className="w-3.5 h-3.5" />
											</button>
										</span>
									{/each}
								</div>
							{/if}

							<div class="relative">
								<input
									id="tag-search"
									type="text"
									value={tagSearchQuery}
									oninput={handleTagSearchInput}
									onfocus={() => (showTagDropdown = true)}
									placeholder="Search tags..."
									class="input"
								/>

								{#if showTagDropdown && (tagSearchResults.length > 0 || searchingTags)}
									<div class="absolute z-10 w-full mt-1 bg-surface-1 border border-line-strong rounded-lg shadow-floating max-h-48 overflow-y-auto">
										{#if searchingTags}
											<div class="px-3 py-2 text-sm text-fg-subtle">Searching...</div>
										{:else}
											{#each tagSearchResults as tag}
												<button
													type="button"
													class="w-full px-3 py-2 text-left text-sm text-fg hover:bg-surface-3 transition-colors"
													onclick={() => addTag(tag)}
												>
													{tag.name}
												</button>
											{/each}
										{/if}
									</div>
								{/if}
							</div>

							{#if availableTags.length > 0 && !tagSearchQuery}
								<div class="flex flex-wrap gap-1 mt-2">
									{#each availableTags.filter((t) => !selectedTags.includes(t.id)).slice(0, 8) as tag}
										<button
											type="button"
											class="px-2 py-0.5 text-xs text-fg-muted bg-surface-3 hover:bg-line-hover border border-line rounded transition-colors"
											onclick={() => addTag(tag)}
										>
											+ {tag.name}
										</button>
									{/each}
								</div>
							{/if}
						</div>

						<!-- SHA256 Checksum -->
						<div>
							<label for="checksum" class="block text-xs font-mono uppercase tracking-[0.06em] text-fg-subtle mb-1.5">
								SHA-256 checksum
							</label>
							<input
								id="checksum"
								type="text"
								bind:value={checksumSha256}
								placeholder="Verify after download (optional)"
								class="input font-mono text-sm"
							/>
						</div>
					</div>
				{/if}
			</div>
		</form>
	{/if}

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 px-6 py-3.5">
			<Button type="button" variant="ghost" onclick={close}>Cancel</Button>
			<Button
				type="button"
				variant="primary"
				icon="download"
				disabled={loadingData || submitting || !url.trim()}
				onclick={handleSubmit}
			>
				{submitting ? 'Queueing…' : 'Queue download'}
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
