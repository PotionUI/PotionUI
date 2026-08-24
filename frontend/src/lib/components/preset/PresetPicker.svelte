<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { PresetInfo } from '$lib/types/api';
	import { availablePresetEngines, filterPresets } from '$lib/utils/presetFilter';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow, PaneGroupHeader } from '$lib/components/pane';
	import PresetThumbnail from './PresetThumbnail.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, EmptyState, Input, Spinner } from '$lib/components/ui';
	import { authStore } from '$lib/stores/auth';
	import { describePresetsEmptyState } from '$lib/utils/presetsEmptyState';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import { api } from '$lib/services/api/index';
	import { formatVramBadge, formatRamBadge, vramShortfall } from '$lib/utils/presetHardware';

	export let presets: PresetInfo[] = [];
	export let selectedPreset: string = '';
	export let loading = false;
	export let disabled = false;
	// Only meaningful when `presets` is empty - explains *why* (no backend, no
	// assignment, …) instead of the generic "no filter matches" message, which
	// is wrong when there was never anything to filter. Fetched once by the
	// parent page on empty-list detection, not polled.
	export let readiness: ReadinessReport | null = null;

	const dispatch = createEventDispatcher<{ select: string }>();

	let open = false;
	let query = '';
	let selectedEngine: string | null = null;
	let selectedCategory: string | null = null;
	let previewPresetId = '';
	// Best-effort GPU VRAM detection for the "needs X GB - this machine has Y GB"
	// warning. `/api/system/stats` is available to any authenticated user (not
	// admin-gated); null stays null on a headless/no-GPU box or a failed fetch,
	// which just suppresses the comparison, never the base requirement badge.
	let detectedVramGb: number | null = null;
	let vramFetchAttempted = false;
	const categoryFilters = [
		{ id: 'image', label: 'Image', icon: 'photo' },
		{ id: 'video', label: 'Video', icon: 'film' },
		{ id: 'audio', label: 'Audio', icon: 'audio' }
	];

	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: safePresets = Array.isArray(presets) ? presets : [];
	$: presetsEmptyState = describePresetsEmptyState(readiness, isAdmin);
	$: hasActiveFilters = query.trim().length > 0 || selectedEngine !== null || selectedCategory !== null;

	function clearFilters() {
		query = '';
		selectedEngine = null;
		selectedCategory = null;
	}
	$: currentPreset = safePresets.find((preset) => preset.id === selectedPreset) ?? null;
	$: engines = availablePresetEngines(safePresets);
	$: if (selectedEngine && !engines.includes(selectedEngine)) selectedEngine = null;
	$: categoryCounts = safePresets.reduce<Record<string, number>>((counts, preset) => {
		const category = preset.category || 'other';
		counts[category] = (counts[category] || 0) + 1;
		return counts;
	}, {});
	$: if (selectedCategory && !categoryCounts[selectedCategory]) selectedCategory = null;
	$: filteredPresets = filterPresets(safePresets, query, selectedEngine).filter(
		(preset) => !selectedCategory || preset.category === selectedCategory
	);
	$: groupedPresets = groupByCategory(filteredPresets);
	$: previewPreset = safePresets.find((preset) => preset.id === previewPresetId) ?? null;
	$: previewVramLabel = formatVramBadge(previewPreset?.requires);
	$: previewRamLabel = formatRamBadge(previewPreset?.requires);
	$: previewVramShortfall = vramShortfall(previewPreset?.requires, detectedVramGb);

	function groupByCategory(items: PresetInfo[]) {
		const categoryOrder = ['image', 'video', 'audio'];
		const labels: Record<string, string> = { image: 'Image', video: 'Video', audio: 'Audio' };
		const groups = new Map<string, PresetInfo[]>();

		for (const preset of items) {
			const category = preset.category || 'other';
			groups.set(category, [...(groups.get(category) || []), preset]);
		}

		return [...groups.entries()]
			.sort(([a], [b]) => {
				const aIndex = categoryOrder.indexOf(a);
				const bIndex = categoryOrder.indexOf(b);
				return (aIndex === -1 ? 99 : aIndex) - (bIndex === -1 ? 99 : bIndex) || a.localeCompare(b);
			})
			.map(([category, categoryPresets]) => ({
				category,
				label: labels[category] || category.charAt(0).toUpperCase() + category.slice(1),
				presets: categoryPresets
			}));
	}

	function openPicker() {
		if (disabled || loading) return;
		previewPresetId = selectedPreset || filteredPresets[0]?.id || safePresets[0]?.id || '';
		open = true;
		loadDetectedVram();
	}

	async function loadDetectedVram() {
		if (vramFetchAttempted) return;
		vramFetchAttempted = true;
		try {
			const response = await api.getClient().get('/api/system/stats');
			const gpu = response.data?.data?.gpu;
			if (gpu?.available && typeof gpu.vram_total === 'number') {
				detectedVramGb = gpu.vram_total / 1024;
			}
		} catch {
			// Comparison is a non-blocking hint - silently skip it on failure.
		}
	}

	function closePicker() {
		open = false;
	}

	function choosePreset() {
		if (!previewPreset) return;
		dispatch('select', previewPreset.id);
		closePicker();
	}
</script>

<button
	type="button"
	class="w-full min-w-0 flex items-center gap-3 px-3 py-2.5 text-left bg-surface-1 border border-line-strong rounded-lg hover:bg-surface-2 hover:border-line-hover focus:ring-1 focus:ring-accent/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
	on:click={openPicker}
	disabled={disabled || loading}
	aria-haspopup="dialog"
>
	{#if loading}
		<Spinner size="sm" />
	{:else if currentPreset}
		<PresetThumbnail
			presetId={currentPreset.id}
			presetName={currentPreset.name}
			cover={currentPreset.media?.cover}
			category={currentPreset.category}
			size="w-9 h-9"
		/>
	{:else}
		<span class="w-9 h-9 flex items-center justify-center rounded-md bg-surface-3 text-fg-subtle">
			<Icon name="cube" className="w-4 h-4" />
		</span>
	{/if}
	<span class="min-w-0 flex-1">
		<span class="block text-sm font-semibold text-fg truncate">{currentPreset?.name || 'Choose a preset'}</span>
		{#if currentPreset}
			<span class="block font-mono text-2xs text-fg-muted truncate">
				{[currentPreset.engine, currentPreset.category, `v${currentPreset.version}`].filter(Boolean).join(' · ')}
			</span>
		{/if}
	</span>
	<Icon name="chevron-down" className="w-4 h-4 text-fg-subtle flex-shrink-0" />
</button>

<BaseModal
	isOpen={open}
	title="Choose preset"
	sizeClass="md:w-[min(94vw,72rem)] md:h-[min(88vh,46rem)]"
	on:close={closePicker}
>
	<div class="h-full min-h-0 flex flex-col">
		<div class="flex flex-col gap-2.5 px-4 py-3 border-b border-line flex-shrink-0 bg-surface-1">
			<div class="flex flex-col sm:flex-row sm:items-center gap-2">
				<div class="flex-1 min-w-0">
					<Input bind:value={query} type="search" placeholder="Search name, category, engine, or tag…" aria-label="Search presets" />
				</div>
				{#if engines.length > 1}
				<div class="flex items-center gap-1 overflow-x-auto" aria-label="Filter by engine">
					<span class="label mb-0 mr-1">Engine</span>
					<button
						type="button"
						class="px-2 py-1.5 text-xs rounded whitespace-nowrap {selectedEngine === null ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2'}"
						on:click={() => (selectedEngine = null)}
						aria-pressed={selectedEngine === null}
					>All</button>
					{#each engines as engine}
						<button
							type="button"
							class="px-2 py-1.5 text-xs rounded whitespace-nowrap {selectedEngine === engine ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2'}"
							on:click={() => (selectedEngine = engine)}
							aria-pressed={selectedEngine === engine}
						>{engine}</button>
					{/each}
				</div>
				{/if}
			</div>

			<div class="flex items-center gap-1 overflow-x-auto" aria-label="Filter by media type">
				<span class="label mb-0 mr-1">Type</span>
				<button
					type="button"
					class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded whitespace-nowrap transition-colors {selectedCategory === null ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2'}"
					on:click={() => (selectedCategory = null)}
					aria-pressed={selectedCategory === null}
				>
					<Icon name="layers" className="w-3.5 h-3.5" />
					All
					<span class="font-mono tabular-nums text-2xs opacity-70">{safePresets.length}</span>
				</button>
				{#each categoryFilters as category}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded whitespace-nowrap transition-colors disabled:opacity-35 disabled:cursor-not-allowed {selectedCategory === category.id ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2'}"
						on:click={() => (selectedCategory = category.id)}
						aria-pressed={selectedCategory === category.id}
						disabled={!categoryCounts[category.id]}
					>
						<Icon name={category.icon} className="w-3.5 h-3.5" />
						{category.label}
						<span class="font-mono tabular-nums text-2xs opacity-70">{categoryCounts[category.id] || 0}</span>
					</button>
				{/each}
			</div>
		</div>

		<div class="flex-1 min-h-0">
			<MasterDetailLayout leftWidth={400} minWidth={300} maxWidth={540} storageKey="preset-picker-list-width">
				<div slot="list" class="h-full">
					<Pane
						label="Presets"
						isEmpty={safePresets.length === 0 || filteredPresets.length === 0}
						bodyRole="listbox"
						ariaLabel="Presets"
					>
						{#snippet empty()}
							{#if safePresets.length === 0}
								<!-- Nothing installed/assigned at all - a "no filter matches" message
									would be wrong here since there was never anything to filter. -->
								<div class="p-4">
									<EmptyState
										icon={presetsEmptyState.showSetupLink ? 'settings' : 'cube'}
										title={presetsEmptyState.title}
										description={presetsEmptyState.action
											? `${presetsEmptyState.message} ${presetsEmptyState.action}`
											: presetsEmptyState.message}
										compact
									>
										{#snippet actions()}
											{#if presetsEmptyState.showSetupLink}
												<Button variant="primary" size="sm" href="/setup" icon="arrow-right">Go to Setup</Button>
											{/if}
										{/snippet}
									</EmptyState>
								</div>
							{:else}
								<div class="p-4 text-sm text-fg-subtle text-center">
									<p>No presets match the current filters.</p>
									{#if hasActiveFilters}
										<button type="button" class="mt-2 text-xs text-signal underline" on:click={clearFilters}>
											Clear filters
										</button>
									{/if}
								</div>
							{/if}
						{/snippet}

						{#snippet children()}
							{#each groupedPresets as group (group.category)}
								<PaneGroupHeader
									icon={group.category === 'image' ? 'photo' : group.category === 'video' ? 'film' : group.category === 'audio' ? 'audio' : 'layers'}
									label={group.label}
									count={group.presets.length}
								/>
								{#each group.presets as preset (preset.id)}
									{#snippet presetLeading()}
										<PresetThumbnail
											presetId={preset.id}
											presetName={preset.name}
											cover={preset.media?.cover}
											category={preset.category}
											size="w-10 h-10"
										/>
									{/snippet}
									{#snippet presetBadges()}
										{#if preset.id === selectedPreset}<Badge variant="signal" size="sm">current</Badge>{/if}
									{/snippet}
									<PaneRow
										selected={previewPresetId === preset.id}
										onclick={() => (previewPresetId = preset.id)}
										leading={presetLeading}
										title={preset.name}
										subtitle="{preset.engine || 'unknown engine'} · v{preset.version}"
										subtitleMono
										badges={presetBadges}
									/>
								{/each}
							{/each}
						{/snippet}
					</Pane>
				</div>

				<div slot="detail" class="h-full overflow-y-auto">
					{#if previewPreset}
						<div class="p-5 sm:p-6 min-h-full flex flex-col">
							<div class="flex flex-col sm:flex-row gap-5">
								<PresetThumbnail
									presetId={previewPreset.id}
									presetName={previewPreset.name}
									cover={previewPreset.media?.cover}
									category={previewPreset.category}
									size="w-32 h-32 sm:w-40 sm:h-40"
								/>
								<div class="min-w-0 flex-1">
									<p class="label mb-1 inline-flex items-center gap-1.5">
										<Icon name={previewPreset.category === 'image' ? 'photo' : previewPreset.category === 'video' ? 'film' : previewPreset.category === 'audio' ? 'audio' : 'layers'} className="w-3.5 h-3.5" />
										{previewPreset.category || 'Preset'}
									</p>
									<h3 class="text-xl font-semibold text-fg">{previewPreset.name}</h3>
									<div class="flex flex-wrap items-center gap-2 mt-2">
										{#if previewPreset.engine}<Badge variant="info">{previewPreset.engine}</Badge>{/if}
										<Badge variant="neutral">v{previewPreset.version}</Badge>
										{#if previewPreset.source}<Badge variant="neutral">{previewPreset.source}</Badge>{/if}
										{#if previewVramShortfall}
											<Badge variant="warning" class="font-mono tabular-nums">{previewVramShortfall}</Badge>
										{:else if previewVramLabel}
											<Badge variant="neutral" class="font-mono tabular-nums text-fg-muted">{previewVramLabel}</Badge>
										{/if}
										{#if previewRamLabel}
											<Badge variant="neutral" class="font-mono tabular-nums text-fg-muted">{previewRamLabel}</Badge>
										{/if}
									</div>
								</div>
							</div>

							<p class="text-sm leading-relaxed text-fg-muted mt-5 whitespace-pre-line">
								{previewPreset.description || 'No description has been provided for this preset.'}
							</p>
							{#if previewPreset.tags?.length}
								<div class="flex flex-wrap gap-1.5 mt-4">
									{#each previewPreset.tags as tag}<Badge variant="neutral" size="sm">{tag}</Badge>{/each}
								</div>
							{/if}

							<div class="mt-auto pt-6 flex justify-end">
								<Button variant="primary" icon="check" onclick={choosePreset}>
									{previewPreset.id === selectedPreset ? 'Keep selected' : 'Use this preset'}
								</Button>
							</div>
						</div>
					{:else}
						<div class="p-4 h-full"><EmptyState title="No preset selected" description="Choose a preset from the list to inspect it." icon="cube" compact /></div>
					{/if}
				</div>
			</MasterDetailLayout>
		</div>
	</div>
</BaseModal>
