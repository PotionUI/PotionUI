<script module lang="ts">
	let detectedVram: Promise<number | null> | null = null;
</script>

<script lang="ts">
	import { onMount, tick } from 'svelte';
	import type { PresetGalleryItem, PresetInfo } from '$lib/types/api';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import { api } from '$lib/services/api/index';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, EmptyState, IconButton, Menu, Spinner } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import DropdownShell from '$lib/components/drawer/DropdownShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import PresetThumbnail from './PresetThumbnail.svelte';
	import PresetExampleCard from './PresetExampleCard.svelte';
	import tooltip from '$lib/actions/tooltip';
	import { authStore } from '$lib/stores/auth';
	import { presetPickerState } from '$lib/stores/presetPickerState';
	import { recipeCatalog, loadRecipeCatalog } from '$lib/stores/recipeCatalog';
	import { describePresetsEmptyState } from '$lib/utils/presetsEmptyState';
	import { resolveInstallModelsTarget } from '$lib/utils/installModelsTarget';
	import { fetchDetectedVramGb, formatRamBadge, formatVramBadge, vramShortfall } from '$lib/utils/presetHardware';
	import { presetCategoryLabel } from '$lib/utils/presetCategories';
	import { processMarkdown } from '$lib/utils/markdown';
	import { logger } from '$lib/utils/logger';
	import { presetIssue, presetSections, type PresetIssue } from './presetDropdownModel';

	let {
		presets = [],
		selectedPreset = '',
		readiness = null,
		isReloading = false,
		anchor,
		fullscreen = false,
		onSelect,
		onReload,
		onClose
	}: {
		presets?: PresetInfo[];
		selectedPreset?: string;
		readiness?: ReadinessReport | null;
		isReloading?: boolean;
		anchor: HTMLElement | undefined;
		fullscreen?: boolean;
		onSelect: (presetId: string) => void;
		onReload: () => void;
		onClose: () => void;
	} = $props();

	const uid = $props.id();

	let root = $state<HTMLElement>();
	let searchEl = $state<HTMLInputElement>();
	let query = $state('');
	let active = $state(-1);
	let view = $state<'list' | 'details'>('list');
	let detailsFor = $state('');
	let gpuGb = $state<number | null>(null);
	let gallery = $state<PresetGalleryItem[]>([]);
	let galleryFor = $state('');
	let galleryLoading = $state(false);

	let isAdmin = $derived($authStore.user?.account_type === 'ADMIN');
	let sections = $derived(presetSections(presets, query, $presetPickerState, selectedPreset));
	let options = $derived(sections.flatMap((section) => section.presets.map((preset) => ({ section: section.key, preset }))));
	let matchCount = $derived(new Set(options.map((option) => option.preset.id)).size);
	let emptyState = $derived(describePresetsEmptyState(readiness, isAdmin));
	let installModels = $derived(resolveInstallModelsTarget($recipeCatalog, null, isAdmin));
	let details = $derived(presets.find((preset) => preset.id === detailsFor) ?? null);
	let detailsIssue = $derived(details ? presetIssue(details, gpuGb) : null);
	let detailsShortfall = $derived(details ? vramShortfall(details.requires, gpuGb) : null);
	let detailsVram = $derived(details ? detailsShortfall ?? formatVramBadge(details.requires) : null);
	let detailsRam = $derived(details ? formatRamBadge(details.requires) : null);
	let detailsHtml = $derived(details?.description ? processMarkdown(details.description) : '');

	onMount(() => {
		void presetPickerState.load();
		if (isAdmin && presets.length === 0) void loadRecipeCatalog();
		detectedVram ??= fetchDetectedVramGb(api.getClient());
		void detectedVram.then((gb) => (gpuGb = gb));
	});

	$effect(() => {
		if (active < 0 || !root) return;
		root.querySelector(`[id="${uid}-opt-${active}"]`)?.scrollIntoView({ block: 'nearest' });
	});

	function choose(presetId: string) {
		if (presetId !== selectedPreset) {
			void presetPickerState.pushRecent(presetId);
			onSelect(presetId);
		}
		onClose();
	}

	async function openDetails(presetId: string) {
		detailsFor = presetId;
		view = 'details';
		if (galleryFor !== presetId) void loadGallery(presetId);
		await tick();
		root?.querySelector<HTMLElement>('[data-details-back] button')?.focus();
	}

	async function backToList() {
		view = 'list';
		await tick();
		searchEl?.focus();
	}

	async function loadGallery(presetId: string) {
		galleryFor = presetId;
		galleryLoading = true;
		gallery = [];
		try {
			const response = await api.getPreset(presetId);
			if (galleryFor === presetId && response.success && response.data) gallery = response.data.media?.gallery ?? [];
		} catch (error) {
			logger.error('Failed to load preset examples:', error);
		} finally {
			if (galleryFor === presetId) galleryLoading = false;
		}
	}

	function handleEscape(): boolean {
		if (view === 'details') {
			void backToList();
			return true;
		}
		if (query) {
			query = '';
			active = -1;
			return true;
		}
		return false;
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		if (options.length === 0) return;
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			active = Math.max(0, Math.min(options.length - 1, active + (event.key === 'ArrowDown' ? 1 : -1)));
		} else if (event.key === 'Home' || event.key === 'End') {
			event.preventDefault();
			active = event.key === 'Home' ? 0 : options.length - 1;
		} else if (event.key === 'Enter') {
			event.preventDefault();
			choose(options[Math.max(0, active)].preset.id);
		}
	}

	function handleInput() {
		active = query.trim() ? 0 : -1;
	}

	function clearSearch() {
		query = '';
		active = -1;
		searchEl?.focus();
	}

	function rowActions(preset: PresetInfo): MenuItem[] {
		const items: MenuItem[] = [
			{ id: 'details', label: 'Details', icon: 'document' },
			{ id: 'pin', label: $presetPickerState.pinned.includes(preset.id) ? 'Unpin' : 'Pin', icon: 'pin' }
		];
		if (isAdmin && preset.id === selectedPreset) {
			items.push(
				{ id: 'divider', label: '', separator: true },
				{ id: 'reload', label: isReloading ? 'Reloading preset' : 'Reload from disk', icon: 'refresh', disabled: isReloading }
			);
		}
		return items;
	}

	function runRowAction(preset: PresetInfo, id: string) {
		if (id === 'details') void openDetails(preset.id);
		else if (id === 'pin') void presetPickerState.togglePin(preset.id);
		else if (id === 'reload') onReload();
	}

	function matchParts(name: string): [string, string, string] | null {
		const needle = query.trim().toLowerCase();
		if (!needle) return null;
		const at = name.toLowerCase().indexOf(needle);
		if (at < 0) return null;
		return [name.slice(0, at), name.slice(at, at + needle.length), name.slice(at + needle.length)];
	}

	function fixLink(preset: PresetInfo, issue: PresetIssue): { href: string; label: string } {
		if (issue.kind === 'backend') return { href: '/admin?tab=backends', label: 'Open backends' };
		return { href: `/admin?${new URLSearchParams({ tab: 'presets', id: preset.id }).toString()}`, label: 'Open requirements' };
	}
</script>

<DropdownShell
	label="Choose preset"
	{anchor}
	width={480}
	fill={presets.length > 0}
	{fullscreen}
	{onClose}
	onEscape={handleEscape}
	bind:root
	bind:searchEl
>
	{#if fullscreen}
		<DrawerHeader title="Choose preset" closeLabel="Close presets" {onClose} />
	{/if}

	{#if presets.length === 0}
		<div class="px-4 py-5 {fullscreen ? 'flex-1' : ''}">
			<EmptyState
				icon={emptyState.showSetupLink ? 'settings' : 'cube'}
				title={emptyState.title}
				description={emptyState.action ? `${emptyState.message} ${emptyState.action}` : emptyState.message}
				compact
			>
				{#snippet actions()}
					{#if emptyState.showSetupLink}
						{#if installModels}
							<Button size="sm" variant="secondary" href={installModels.href} icon="download">{installModels.label}</Button>
						{:else}
							<Button size="sm" variant="secondary" href="/setup" icon="arrow-right">Go to Setup</Button>
						{/if}
					{/if}
				{/snippet}
			</EmptyState>
		</div>
	{:else if view === 'details' && details}
		<div class="flex h-11 flex-shrink-0 items-center gap-1.5 border-b border-line pl-1.5 pr-3.5">
			<span class="flex" data-details-back>
				<Tooltip text="Back to presets" kbd="Esc" position="bottom" delay={150}>
					<IconButton icon="chevron-left" label="Back to presets" size="sm" onclick={backToList} />
				</Tooltip>
			</span>
			<span class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{details.name}</span>
		</div>
		<div class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-4 pt-3.5" data-testid="preset-details">
			<div class="flex items-start gap-3">
				<PresetThumbnail
					presetId={details.id}
					presetName={details.name}
					cover={details.media?.cover}
					category={details.category}
					size="w-[72px] h-[72px] rounded-lg"
					zoom={false}
				/>
				<div class="min-w-0">
					<p class="m-0 font-mono text-sm text-fg-subtle">
						{presetCategoryLabel(details.category)} · {details.engine || 'unknown engine'} · v{details.version}
					</p>
					<p class="m-0 mt-0.5 text-md font-semibold text-fg">{details.name}</p>
					<div class="mt-2 flex flex-wrap gap-1.5">
						{#if detailsVram}
							<span
								class="inline-flex h-[22px] items-center rounded border px-2 font-mono text-sm {detailsShortfall
									? 'border-warning/50 text-warning'
									: 'border-line-strong text-fg-muted'}">{detailsVram}</span
							>
						{/if}
						{#if detailsRam}
							<span class="inline-flex h-[22px] items-center rounded border border-line-strong px-2 font-mono text-sm text-fg-muted">{detailsRam}</span>
						{/if}
						{#if details.tags?.length}
							<span class="inline-flex h-[22px] items-center rounded border border-line-strong px-2 font-mono text-sm text-fg-muted">{details.tags.join(' · ')}</span>
						{/if}
					</div>
				</div>
			</div>
			{#if detailsIssue?.blocking}
				{@const link = fixLink(details, detailsIssue)}
				<div class="flex flex-col gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5 text-sm leading-5 text-fg" data-testid="preset-issue">
					<span><span class="font-semibold">{detailsIssue.label}.</span> {detailsIssue.detail}</span>
					{#if isAdmin}
						<span><Button variant="secondary" size="sm" href={link.href}>{link.label}</Button></span>
					{:else}
						<span class="text-fg-muted">Ask your administrator to set it up.</span>
					{/if}
				</div>
			{/if}
			{#if detailsHtml}
				<div class="text-base leading-relaxed text-fg-muted">{@html detailsHtml}</div>
			{:else}
				<p class="m-0 text-base leading-relaxed text-fg-muted">No description has been provided for this preset.</p>
			{/if}
			<div data-testid="preset-examples">
				<p class="m-0 mb-1.5 text-sm text-fg-subtle">Examples</p>
				{#if galleryLoading}
					<div class="flex justify-center py-4"><Spinner size="sm" /></div>
				{:else if gallery.length === 0}
					<p class="m-0 text-sm text-fg-subtle">No examples yet.</p>
				{:else}
					<div class="grid grid-cols-3 gap-1.5">
						{#each gallery as item}
							<PresetExampleCard presetId={details.id} presetName={details.name} {item} />
						{/each}
					</div>
				{/if}
			</div>
		</div>
		<div class="flex min-h-12 flex-shrink-0 items-center gap-1.5 border-t border-line px-2.5 py-2">
			{#if isAdmin && details.id === selectedPreset}
				<Button variant="secondary" size="sm" icon="refresh" disabled={isReloading} onclick={onReload}>
					{isReloading ? 'Reloading preset' : 'Reload from disk'}
				</Button>
			{/if}
			<span class="flex-1"></span>
			<Button variant="primary" size="sm" icon="check" onclick={() => choose(details.id)}>
				{details.id === selectedPreset ? 'Keep selected' : 'Use this preset'}
			</Button>
		</div>
	{:else}
		<div class="flex h-11 flex-shrink-0 items-center gap-2.5 border-b border-line pl-3.5 pr-2">
			<Icon name="search" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<input
				bind:this={searchEl}
				bind:value={query}
				type="text"
				role="combobox"
				aria-expanded="true"
				aria-controls="{uid}-list"
				aria-autocomplete="list"
				aria-activedescendant={active >= 0 ? `${uid}-opt-${active}` : undefined}
				class="min-w-0 flex-1 border-0 bg-transparent py-0 pl-0.5 pr-0 text-base text-fg outline-none placeholder:text-fg-subtle focus:ring-0"
				placeholder="Search {presets.length} presets: name, family, engine or tag"
				aria-label="Search presets"
				autocomplete="off"
				data-autofocus={fullscreen ? undefined : ''}
				oninput={handleInput}
				onkeydown={handleSearchKeydown}
			/>
			{#if query}
				<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{matchCount} of {presets.length}</span>
				<Tooltip text="Clear search" position="bottom" delay={150}>
					<IconButton icon="close" label="Clear search" size="xs" onclick={clearSearch} />
				</Tooltip>
			{/if}
		</div>

		<div
			class="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-2"
			role="listbox"
			aria-label="Presets"
			id="{uid}-list"
			data-testid="preset-list-scroll"
		>
			{#if options.length === 0}
				<div class="flex flex-col items-start gap-1 px-4 py-5">
					<p class="m-0 text-base font-semibold text-fg">No presets match “{query.trim()}”</p>
					<p class="m-0 text-sm text-fg-muted">Search covers names, families, engines and tags.</p>
					<Button variant="secondary" size="sm" class="mt-3" onclick={clearSearch}>Clear search</Button>
				</div>
			{:else}
				{@const starts = sections.map((_, index) => sections.slice(0, index).reduce((sum, section) => sum + section.presets.length, 0))}
				{#each sections as section, sectionIndex (section.key)}
					<div
						class="sticky top-0 z-[1] flex items-center gap-1.5 bg-surface-1 px-3.5 pb-1 pt-2.5 text-sm leading-5 text-fg-subtle"
						role="presentation"
						data-preset-group={section.key}
					>
						<Icon name={section.icon} className="h-3.5 w-3.5 flex-shrink-0" />
						<span data-preset-group-label>{section.label}</span>
						<span class="ml-auto font-mono tabular-nums">{section.presets.length}</span>
					</div>
					<ul
						class="m-0 list-none p-0"
						role="group"
						aria-label={section.label}
						data-preset-family={section.family ? section.key : undefined}
					>
						{#each section.presets as preset, rowIndex (preset.id)}
							{@render row(preset, starts[sectionIndex] + rowIndex)}
						{/each}
					</ul>
				{/each}
			{/if}
		</div>

		{#if isAdmin}
			<div class="flex min-h-12 flex-shrink-0 items-center gap-1.5 border-t border-line px-2.5 py-2">
				<Button variant="secondary" size="sm" icon="settings" href="/admin?tab=presets">Manage presets</Button>
				<span class="ml-auto font-mono text-sm tabular-nums text-fg-subtle">{presets.length} presets</span>
			</div>
		{/if}
	{/if}
</DropdownShell>

{#snippet row(preset: PresetInfo, index: number)}
	{@const current = preset.id === selectedPreset}
	{@const issue = presetIssue(preset, gpuGb)}
	{@const parts = matchParts(preset.name)}
	<li
		class="preset-row relative mx-1.5 flex items-center rounded"
		class:is-current={current}
		class:is-active={index === active}
		role="option"
		id="{uid}-opt-{index}"
		aria-labelledby="{uid}-name-{index}"
		aria-describedby="{uid}-sub-{index}"
		aria-selected={current}
		data-preset-row={preset.id}
	>
		<button
			type="button"
			tabindex="-1"
			class="row-main flex min-w-0 flex-1 items-center gap-3 rounded pl-2 pr-1 text-left {fullscreen ? 'h-14' : 'h-[52px]'}"
			use:tooltip={issue ? { text: issue.detail, position: 'right', delay: 300 } : null}
			onclick={() => choose(preset.id)}
		>
			<span class="flex flex-shrink-0 {issue?.blocking ? 'opacity-[0.45]' : ''}">
				<PresetThumbnail
					presetId={preset.id}
					presetName={preset.name}
					cover={preset.media?.cover}
					category={preset.category}
					size="{fullscreen ? 'w-11 h-11' : 'w-10 h-10'} rounded-lg"
					zoom={false}
				/>
			</span>
			<span class="flex min-w-0 flex-1 flex-col gap-0.5">
				<span id="{uid}-name-{index}" class="truncate text-base font-medium leading-[18px] {issue?.blocking ? 'text-fg-muted' : 'text-fg'}">
					{#if parts}{parts[0]}<b class="font-semibold text-fg">{parts[1]}</b>{parts[2]}{:else}{preset.name}{/if}
				</span>
				{#if issue}
					<span
						id="{uid}-sub-{index}"
						class="flex min-w-0 items-center gap-1.5 text-sm leading-4 {issue.kind === 'backend' ? 'text-danger' : 'text-warning'}"
						data-preset-issue={issue.kind}
					>
						<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-current" aria-hidden="true"></span>
						<span class="truncate">{issue.label}</span>
					</span>
				{:else}
					<span id="{uid}-sub-{index}" class="truncate font-mono text-sm leading-4 text-fg-subtle">{preset.engine || 'unknown engine'} · v{preset.version}</span>
				{/if}
			</span>
			{#if current}
				<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-signal" />
			{/if}
		</button>
		<span class="row-more mr-1 flex flex-shrink-0" class:always={fullscreen}>
			<Menu
				label="More actions for {preset.name}"
				menuLabel="Actions for {preset.name}"
				size="sm"
				width={200}
				items={rowActions(preset)}
				onselect={(id) => runRowAction(preset, id)}
			/>
		</span>
	</li>
{/snippet}

<style>
	.preset-row:hover {
		background: rgb(var(--surface-2));
	}

	.preset-row.is-active {
		background: rgb(var(--surface-3));
	}

	.preset-row.is-current {
		background: rgb(var(--signal) / 0.1);
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.preset-row.is-current.is-active,
	.preset-row.is-current:hover {
		background: rgb(var(--signal) / 0.16);
	}

	:global(:where(html:not([data-input='pointer']))) .row-main:focus-visible {
		outline: none;
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.6);
	}

	.row-more {
		opacity: 0;
		transition: opacity 100ms;
	}

	.preset-row:hover .row-more,
	.preset-row:focus-within .row-more,
	.preset-row.is-active .row-more,
	.row-more:has(:global([aria-expanded='true'])),
	.row-more.always {
		opacity: 1;
	}
</style>
