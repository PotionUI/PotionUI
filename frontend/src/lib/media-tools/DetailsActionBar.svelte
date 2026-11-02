<script lang="ts" module>
	export interface BarAction {
		id: string;
		label: string;
		icon: string;
		onclick: () => void;
		tone?: 'danger';
		disabled?: boolean;
		loading?: boolean;
		tooltip?: string;
		description?: string;
		shortcut?: string;
	}

	export interface BarTags {
		selectedIds: string[];
		tagType?: 'GENERATION' | 'MODEL' | 'UPLOAD';
		onChange: (ids: string[]) => void;
	}

	export interface BarFavorite {
		active: boolean;
		onToggle: () => void;
	}
</script>

<script lang="ts">
	import { onMount, tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import { getIconPath } from '$lib/utils/IconLibrary';
	import TabFooter from '$lib/tool-tabs/TabFooter.svelte';
	import { openToolShortcut } from '$lib/tool-tabs/openShortcut';
	import { uniqueTargets } from '$lib/tool-tabs/shortcuts';
	import { recentLooks } from '$lib/filters/recentLooks';
	import { loadPluginMediaTools, mediaToolRegistrations } from '$lib/tools/pluginTools';
	import { buildRowContext, buildRowModel, type RowTool } from '$lib/tools/rowModel';
	import type { MediaToolContext, MediaToolItem as RowItem } from '$lib/tools/tools';
	import { landOnHistoryResult } from '$lib/tools/historyResult';
	import { returnToDetails } from '$lib/tools/detailsReturn';
	import { trackLaunch } from '$lib/tools/launchTracker';
	import type { LandingSource } from '$lib/library/landing';
	import type { MediaToolItem as ToolResultItem } from './types';
	import { openMediaTool, type MediaToolResultInfo } from './open';
	import { foldedIds } from './barLayout';
	import BarMenu from './BarMenu.svelte';

	interface MenuRow {
		id: string;
		label: string;
		icon: string;
		sub?: string;
		plugin?: boolean;
		kbd?: string;
		tone?: 'danger';
		disabled?: boolean;
		loading?: boolean;
		on?: boolean;
		count?: number;
		opens?: boolean;
		run: () => void;
	}

	let {
		items,
		scope,
		context = null,
		favorite = null,
		tags = null,
		pageActions = [],
		downloadActions = [],
		moreActions = [],
		dangerActions = [],
		pluginContext = null,
		onResult,
		onLaunch,
		onOpenedInTab
	}: {
		items: RowItem[];
		scope: 'history' | 'library';
		context?: MediaToolContext | null;
		favorite?: BarFavorite | null;
		tags?: BarTags | null;
		pageActions?: BarAction[];
		downloadActions?: BarAction[];
		moreActions?: BarAction[];
		dangerActions?: BarAction[];
		pluginContext?: Record<string, unknown> | null;
		onResult?: (items: ToolResultItem[], info: MediaToolResultInfo) => void;
		onLaunch?: () => void;
		onOpenedInTab?: () => void;
	} = $props();

	const starPath = getIconPath('star') as string;
	const BAR_PADDING = 12;

	let barEl = $state<HTMLElement>();
	let ghostEl = $state<HTMLElement>();
	let editOpen = $state(false);
	let effectsOpen = $state(false);
	let downloadOpen = $state(false);
	let moreOpen = $state(false);
	let tagsOpen = $state(false);
	let sheet = $state(false);
	let available = $state(0);
	let widths = $state<Record<string, number>>({});
	let activeId = $state('edit');

	let toolContext = $derived(context ?? buildRowContext(scope, items));

	let model = $derived.by(() => {
		void $mediaToolRegistrations;
		return buildRowModel(toolContext, $recentLooks);
	});

	let downloadPluginRows = $derived(model.downloads);
	let downloadCount = $derived(downloadActions.length + downloadPluginRows.length);
	let plainDownload = $derived(downloadActions.length === 1 && downloadPluginRows.length === 0);
	let hasDownload = $derived(downloadCount > 0);
	let excludedPlugins = $derived(
		model.downloads.flatMap((entry) => (entry.pluginId ? [entry.pluginId] : []))
	);

	let folded = $derived(
		foldedIds({
			available,
			widths,
			favorite: !!favorite,
			tags: !!tags,
			pageIds: pageActions.map((action) => action.id),
			download: hasDownload,
			effects: model.effects.length > 0,
			edit: model.edit.length > 0
		})
	);

	let visibleIds = $derived([
		...(model.edit.length > 0 ? ['edit'] : []),
		...(model.effects.length > 0 ? ['effects'] : []),
		...(favorite && !folded.has('favorite') ? ['favorite'] : []),
		...(tags && !folded.has('tags') ? ['tags'] : []),
		...pageActions.filter((action) => !folded.has(action.id)).map((action) => action.id),
		...(hasDownload && !folded.has('download') ? ['download'] : []),
		'more'
	]);

	let currentId = $derived(visibleIds.includes(activeId) ? activeId : (visibleIds[0] ?? 'more'));

	onMount(() => {
		void loadPluginMediaTools();
		const query = window.matchMedia?.('(max-width: 639px)');
		if (query) {
			sheet = query.matches;
			const listener = (event: MediaQueryListEvent) => (sheet = event.matches);
			query.addEventListener?.('change', listener);
			return () => query.removeEventListener?.('change', listener);
		}
	});

	function measure(width?: number) {
		if (barEl) {
			const total = width ?? barEl.getBoundingClientRect().width;
			if (total > 0) available = Math.max(0, total - BAR_PADDING);
		}
		if (!ghostEl) return;
		const next: Record<string, number> = {};
		for (const node of ghostEl.querySelectorAll<HTMLElement>('[data-bar-measure]')) {
			next[node.dataset.barMeasure ?? ''] = node.getBoundingClientRect().width;
		}
		const same =
			Object.keys(next).length === Object.keys(widths).length &&
			Object.entries(next).every(([key, value]) => widths[key] === value);
		if (!same) widths = next;
	}

	onMount(() => {
		if (!barEl || typeof ResizeObserver === 'undefined') return;
		const observer = new ResizeObserver((entries) => {
			const entry = entries[entries.length - 1];
			measure(entry?.contentRect?.width ? entry.contentRect.width + BAR_PADDING : undefined);
		});
		observer.observe(barEl);
		measure();
		return () => observer.disconnect();
	});

	$effect(() => {
		void [model, pageActions, downloadActions, favorite?.active, tags?.selectedIds.length];
		void tick().then(() => measure());
	});

	function launchSource(): LandingSource | null {
		const first = items[0];
		if (!first?.generationId) return null;
		return { generationId: first.generationId, fileIndex: first.paramIndex ?? 0 };
	}

	function historyHooks() {
		const source = launchSource();
		const startPath = typeof window === 'undefined' ? '' : window.location.pathname;
		const tracker = trackLaunch(() => {
			if (source) returnToDetails(source);
		});
		return {
			tracker,
			onClose: tracker.closed,
			land: (landed: { id: string }[]) => {
				tracker.delivered();
				return landOnHistoryResult(landed as never, { source, startPath });
			}
		};
	}

	function run(entry: RowTool) {
		if (!entry.enabled) return;
		if (entry.opens && openToolShortcut(entry.id, toolContext.items)) {
			onOpenedInTab?.();
			return;
		}
		const toolId = entry.tool?.catalogToolId;
		if (!toolId) return;
		if (scope !== 'history') {
			void openMediaTool({
				toolId,
				items,
				scope,
				context: toolContext,
				onResult: (results: ToolResultItem[], info: MediaToolResultInfo) => onResult?.(results, info)
			});
			return;
		}
		onLaunch?.();
		const hooks = historyHooks();
		void openMediaTool({
			toolId,
			items,
			scope,
			context: toolContext,
			announce: { quietSuccess: true, retry: () => run(entry) },
			onClose: hooks.onClose,
			onResult: (results: ToolResultItem[], info: MediaToolResultInfo) => {
				void hooks.land(results);
				onResult?.(results, info);
			}
		});
	}

	export function triggerShortcut(key: string): boolean {
		const letter = key.toLowerCase();
		if (letter === 'e') {
			const entry = model.edit.find((candidate) => candidate.action === 'edit-image');
			if (!entry?.enabled) return false;
			run(entry);
			return true;
		}
		if (letter === 'd') {
			const action = downloadActions.find((candidate) => candidate.shortcut?.toLowerCase() === 'd' && !candidate.disabled);
			if (!action) return false;
			action.onclick();
			return true;
		}
		if (letter === 'f') {
			if (!favorite) return false;
			favorite.onToggle();
			return true;
		}
		return false;
	}

	function subline(entry: RowTool): string {
		return entry.enabled ? entry.note : entry.reason;
	}

	function barItems(): HTMLElement[] {
		return barEl ? Array.from(barEl.querySelectorAll<HTMLElement>('[data-bar-item]')) : [];
	}

	function focusNeighbour(id: string, direction: -1 | 1) {
		const all = barItems();
		const index = all.findIndex((node) => node.dataset.barItem === id);
		if (index < 0 || all.length === 0) return;
		all[(index + direction + all.length) % all.length]?.focus();
	}

	function handleBarKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement | null;
		if (!target || !target.hasAttribute('data-bar-item')) return;
		const all = barItems();
		const index = all.indexOf(target);
		if (index < 0) return;
		let next = -1;
		if (event.key === 'ArrowRight') next = (index + 1) % all.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + all.length) % all.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = all.length - 1;
		else if (event.key === 'ArrowDown' && target.hasAttribute('aria-haspopup')) {
			event.preventDefault();
			if (target.getAttribute('aria-expanded') !== 'true') target.click();
			return;
		}
		if (next < 0) return;
		event.preventDefault();
		all[next]?.focus();
	}

	function handleFocusIn(event: FocusEvent) {
		const id = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-bar-item]')?.dataset.barItem;
		if (id) activeId = id;
	}

	function toRow(action: BarAction): MenuRow {
		return {
			id: action.id,
			label: action.label,
			icon: action.icon,
			sub: action.description,
			kbd: action.shortcut,
			tone: action.tone,
			disabled: action.disabled || action.loading,
			loading: action.loading,
			run: action.onclick
		};
	}

	function toolRow(entry: RowTool): MenuRow {
		return {
			id: entry.id,
			label: entry.label,
			icon: entry.icon,
			sub: subline(entry),
			plugin: entry.plugin,
			disabled: !entry.enabled,
			opens: !!entry.opens,
			run: () => run(entry)
		};
	}

	function pluginDownloadRow(entry: RowTool): MenuRow {
		return { ...toolRow(entry), plugin: true };
	}

	let moreGroups = $derived.by(() => {
		const groups: MenuRow[][] = [];
		const lead: MenuRow[] = [];
		if (favorite && folded.has('favorite')) {
			lead.push({
				id: 'favorite',
				label: 'Favorite',
				icon: 'star',
				kbd: 'F',
				on: favorite.active,
				run: favorite.onToggle
			});
		}
		if (tags && folded.has('tags')) {
			lead.push({
				id: 'tags',
				label: 'Tags…',
				icon: 'tag',
				count: tags.selectedIds.length > 0 ? tags.selectedIds.length : undefined,
				run: () => {
					tagsOpen = true;
				}
			});
		}
		if (lead.length > 0) groups.push(lead);
		const page = pageActions.filter((action) => folded.has(action.id)).map(toRow);
		if (page.length > 0) groups.push(page);
		if (hasDownload && folded.has('download')) {
			groups.push([...downloadActions.map(toRow), ...downloadPluginRows.map(pluginDownloadRow)]);
		}
		if (moreActions.length > 0) groups.push(moreActions.map(toRow));
		if (model.plugins.length > 0) groups.push(model.plugins.map(pluginDownloadRow));
		return groups;
	});

	let sheetSubtitle = $derived(items[0]?.filename ?? '');

	let rowPad = $derived(sheet ? 'py-3 text-base' : 'py-1.5 text-sm');

	const barButton =
		'!h-8 !gap-1 !px-2 !py-0 !text-[12px] max-sm:!h-9 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal';
	const borderedButton = `${barButton} border border-line-strong !bg-surface-2 !text-fg hover:border-line-hover hover:!bg-surface-3`;
	const plainButton = `${barButton} !bg-transparent hover:!bg-surface-3/50`;
</script>

{#snippet starIcon(active: boolean)}
	<svg
		class="h-4 w-4 shrink-0"
		viewBox="0 0 24 24"
		fill={active ? 'currentColor' : 'none'}
		stroke="currentColor"
		stroke-width="2"
		aria-hidden="true"
	>
		<path stroke-linecap="round" stroke-linejoin="round" d={starPath} />
	</svg>
{/snippet}

{#snippet editBody()}
	<Icon name="sliders" className="h-4 w-4" />
	Edit
	<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
{/snippet}

{#snippet effectsBody()}
	<Icon name="palette" className="h-4 w-4" />
	Effects
	<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
{/snippet}

{#snippet favoriteBody()}
	{@render starIcon(!!favorite?.active)}
	Favorite
{/snippet}

{#snippet tagsBody()}
	<Icon name="tag" className="h-4 w-4" />
	Tags
	{#if (tags?.selectedIds.length ?? 0) > 0}
		<span class="font-mono text-[12px] leading-4 tabular-nums text-fg-subtle">{tags?.selectedIds.length}</span>
	{/if}
{/snippet}

{#snippet downloadBody()}
	<Icon name="download" className="h-4 w-4" />
	Download
	{#if !plainDownload}
		<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
	{/if}
{/snippet}

{#snippet moreBody()}
	More
	<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
{/snippet}

{#snippet menuRow(row: MenuRow, close: (restoreFocus?: boolean) => void)}
	<button
		type="button"
		role="menuitem"
		class="flex w-full items-center gap-2.5 rounded px-2.5 text-left transition-colors focus-visible:outline-none {rowPad} {row.disabled
			? 'cursor-not-allowed text-fg-muted opacity-40'
			: row.tone === 'danger'
				? 'text-danger hover:bg-danger/10 focus-visible:bg-danger/10'
				: 'text-fg hover:bg-surface-3 focus-visible:bg-surface-3'}"
		aria-disabled={row.disabled ? 'true' : undefined}
		data-bar-row={row.id}
		onclick={() => {
			if (row.disabled) return;
			close(true);
			row.run();
		}}
	>
		{#if row.icon}
			{#if row.icon === 'star'}
				{@render starIcon(!!row.on)}
			{:else}
				<Icon name={row.icon} className="h-4 w-4 shrink-0" />
			{/if}
		{:else}
			<span class="h-4 w-4 shrink-0" aria-hidden="true"></span>
		{/if}
		<span class="flex min-w-0 flex-1 flex-col">
			<span class="truncate" data-bar-row-label>{row.label}</span>
			{#if row.sub || row.plugin}
				<span class="flex min-w-0 items-center gap-1.5 text-[12px] leading-4 text-fg-subtle" data-bar-row-sub>
					{#if row.plugin}
						<span class="shrink-0 rounded border border-line px-1 font-mono uppercase tracking-[0.07em]">Plugin</span>
					{/if}
					{#if row.sub}<span class="truncate">{row.sub}</span>{/if}
				</span>
			{/if}
		</span>
		{#if row.count}
			<span class="shrink-0 font-mono text-[12px] leading-4 tabular-nums text-fg-subtle">{row.count}</span>
		{:else if row.on}
			<Icon name="check" className="h-3.5 w-3.5 shrink-0 text-signal" />
		{:else if row.kbd && !sheet}
			<Kbd keys={row.kbd} />
		{:else if row.opens}
			<span class="shrink-0 text-fg-subtle" data-opens-elsewhere><Icon name="external-link" className="h-3.5 w-3.5" /></span>
		{/if}
	</button>
{/snippet}

{#snippet toolRows(entries: RowTool[], close: (restoreFocus?: boolean) => void)}
	{@const firstPlugin = entries.findIndex((entry) => entry.plugin)}
	{#each entries as entry, index (entry.id)}
		{#if index > 0 && (index === firstPlugin || entry.action === 'edit-image')}
			<div class="my-1 h-px bg-line" role="separator" data-bar-divider-row></div>
		{/if}
		{@render menuRow({ ...toolRow(entry), kbd: entry.action === 'edit-image' ? 'E' : undefined }, close)}
	{/each}
	<TabFooter targets={uniqueTargets(entries.map((entry) => entry.opens))} count={items.length} />
{/snippet}

{#snippet ghost()}
	<div
		bind:this={ghostEl}
		class="pointer-events-none invisible absolute left-0 top-0 flex h-0 w-max gap-1 overflow-hidden whitespace-nowrap [&>*]:shrink-0"
		aria-hidden="true"
		inert
		data-bar-ghost
	>
		{#if model.edit.length > 0}
			<span data-bar-measure="edit" class="inline-flex h-8 items-center gap-1 border px-2 text-[12px] font-medium">{@render editBody()}</span>
		{/if}
		{#if model.effects.length > 0}
			<span data-bar-measure="effects" class="inline-flex h-8 items-center gap-1 border px-2 text-[12px] font-medium">{@render effectsBody()}</span>
		{/if}
		{#if favorite}
			<span data-bar-measure="favorite" class="inline-flex h-8 items-center gap-1 px-2 text-[12px] font-medium">{@render favoriteBody()}</span>
		{/if}
		{#if tags}
			<span data-bar-measure="tags" class="inline-flex h-8 items-center gap-1 px-2 text-[12px] font-medium">{@render tagsBody()}</span>
		{/if}
		{#each pageActions as action (action.id)}
			<span data-bar-measure={action.id} class="inline-flex h-8 items-center gap-1 px-2 text-[12px] font-medium">
				<Icon name={action.icon} className="h-4 w-4" />
				{action.label}
			</span>
		{/each}
		{#if hasDownload}
			<span data-bar-measure="download" class="inline-flex h-8 items-center gap-1 px-2 text-[12px] font-medium">{@render downloadBody()}</span>
		{/if}
		<span data-bar-measure="more" class="inline-flex h-8 items-center gap-1 px-2 text-[12px] font-medium">{@render moreBody()}</span>
	</div>
{/snippet}

{#if model.edit.length > 0 || model.effects.length > 0 || favorite || tags || pageActions.length > 0 || hasDownload || moreActions.length > 0 || dangerActions.length > 0 || pluginContext}
	<div
		bind:this={barEl}
		class="relative flex h-11 min-w-0 shrink-0 items-center gap-1 border-b border-line bg-surface-1 px-1.5 max-sm:h-12"
		role="toolbar"
		aria-label="Actions"
		tabindex="-1"
		data-details-action-bar
		onkeydown={handleBarKeydown}
		onfocusin={handleFocusIn}
	>
		{@render ghost()}

		{#if model.edit.length > 0}
			<BarMenu bind:open={editOpen} label="Edit" {sheet} width={256} subtitle={sheetSubtitle} onbarnav={(direction) => focusNeighbour('edit', direction)}>
				{#snippet trigger({ open, toggle })}
					<Button
						variant="secondary"
						size="sm"
						class={borderedButton}
						ariaExpanded={open}
						ariaHaspopup="menu"
						tabindex={currentId === 'edit' ? 0 : -1}
						attrs={{ 'data-bar-item': 'edit' }}
						onclick={toggle}
					>
						{@render editBody()}
					</Button>
				{/snippet}
				{#snippet children({ close })}
					{@render toolRows(model.edit, close)}
				{/snippet}
			</BarMenu>
		{/if}

		{#if model.effects.length > 0}
			<BarMenu bind:open={effectsOpen} label="Effects" {sheet} width={256} subtitle={sheetSubtitle} onbarnav={(direction) => focusNeighbour('effects', direction)}>
				{#snippet trigger({ open, toggle })}
					<Button
						variant="secondary"
						size="sm"
						class={borderedButton}
						ariaExpanded={open}
						ariaHaspopup="menu"
						tabindex={currentId === 'effects' ? 0 : -1}
						attrs={{ 'data-bar-item': 'effects' }}
						onclick={toggle}
					>
						{@render effectsBody()}
					</Button>
				{/snippet}
				{#snippet children({ close })}
					{@render toolRows(model.effects, close)}
				{/snippet}
			</BarMenu>
		{/if}

		{#if (favorite && !folded.has('favorite')) || (tags && !folded.has('tags'))}
			<span class="mx-[3px] h-5 w-px shrink-0 bg-line" aria-hidden="true" data-bar-divider></span>
		{/if}

		{#if favorite && !folded.has('favorite')}
			<Tooltip text="Mark as a favorite" kbd="F" position="bottom">
				<Button
					variant="ghost"
					size="sm"
					class="{plainButton} {favorite.active ? '!bg-signal/10 !text-signal' : ''}"
					tabindex={currentId === 'favorite' ? 0 : -1}
					attrs={{
						'data-bar-item': 'favorite',
						'data-library-favorite': scope === 'library' ? '' : undefined,
						'aria-pressed': favorite.active ? 'true' : 'false'
					}}
					onclick={favorite.onToggle}
				>
					{@render favoriteBody()}
				</Button>
			</Tooltip>
		{/if}

		{#if tags && !folded.has('tags')}
			<TagSelector
				bind:open={tagsOpen}
				selectedTagIds={tags.selectedIds}
				tagType={tags.tagType ?? 'GENERATION'}
				allowCreate={true}
				compact={true}
				triggerStyle="bar"
				triggerTabindex={currentId === 'tags' ? 0 : -1}
				placeholder="Add tags..."
				on:change={(event) => tags?.onChange(event.detail)}
			/>
		{/if}

		{#if pageActions.some((action) => !folded.has(action.id))}
			<span class="mx-[3px] h-5 w-px shrink-0 bg-line" aria-hidden="true" data-bar-divider></span>
		{/if}

		{#each pageActions.filter((action) => !folded.has(action.id)) as action (action.id)}
			<Tooltip text={action.tooltip ?? ''} position="bottom">
				<Button
					variant="ghost"
					size="sm"
					icon={action.icon}
					class={plainButton}
					disabled={action.disabled}
					loading={action.loading}
					tabindex={currentId === action.id ? 0 : -1}
					attrs={{ 'data-bar-item': action.id }}
					onclick={action.onclick}
				>
					{action.label}
				</Button>
			</Tooltip>
		{/each}

		<div class="ml-auto flex shrink-0 items-center gap-1 pl-1">
			{#if hasDownload && !folded.has('download')}
				{#if plainDownload}
					{@const only = downloadActions[0]}
					<Tooltip text={only.tooltip ?? ''} kbd={only.shortcut} position="bottom">
						<Button
							variant="ghost"
							size="sm"
							class={plainButton}
							disabled={only.disabled}
							tabindex={currentId === 'download' ? 0 : -1}
							attrs={{ 'data-bar-item': 'download' }}
							onclick={only.onclick}
						>
							{@render downloadBody()}
						</Button>
					</Tooltip>
				{:else}
					<BarMenu
						bind:open={downloadOpen}
						label="Download"
						{sheet}
						subtitle={sheetSubtitle}
						align="right"
						onbarnav={(direction) => focusNeighbour('download', direction)}
					>
						{#snippet trigger({ open, toggle })}
							<Button
								variant="ghost"
								size="sm"
								class={plainButton}
								ariaExpanded={open}
								ariaHaspopup="menu"
								tabindex={currentId === 'download' ? 0 : -1}
								attrs={{ 'data-bar-item': 'download' }}
								onclick={toggle}
							>
								{@render downloadBody()}
							</Button>
						{/snippet}
						{#snippet children({ close })}
							{#each downloadActions as action (action.id)}
								{@render menuRow(toRow(action), close)}
							{/each}
							{#if downloadPluginRows.length > 0 && downloadActions.length > 0}
								<div class="my-1 h-px bg-line" role="separator" data-bar-divider-row></div>
							{/if}
							{#each downloadPluginRows as entry (entry.id)}
								{@render menuRow(pluginDownloadRow(entry), close)}
							{/each}
						{/snippet}
					</BarMenu>
				{/if}
			{/if}

			<div class="relative">
				<BarMenu bind:open={moreOpen} label="More" {sheet} subtitle={sheetSubtitle} align="right" onbarnav={(direction) => focusNeighbour('more', direction)}>
					{#snippet trigger({ open, toggle })}
						<Button
							variant="ghost"
							size="sm"
							class={plainButton}
							ariaExpanded={open}
							ariaHaspopup="menu"
							tabindex={currentId === 'more' ? 0 : -1}
							attrs={{ 'data-bar-item': 'more' }}
							onclick={toggle}
						>
							{@render moreBody()}
						</Button>
					{/snippet}
					{#snippet children({ close })}
						{#each moreGroups as group, index (index)}
							{#if index > 0}
								<div class="my-1 h-px bg-line" role="separator" data-bar-divider-row></div>
							{/if}
							{#each group as row (row.id)}
								{@render menuRow(row, close)}
							{/each}
						{/each}
						{#if pluginContext}
							<div
								class="my-1 hidden border-t border-line px-1 pt-1 [&:has(.plugin-slot)]:block"
								data-bar-plugin-actions
							>
								<PluginSlot
									hookName="image.actions"
									context={pluginContext}
									excludePlugins={excludedPlugins}
								/>
							</div>
						{/if}
						{#if dangerActions.length > 0}
							{#if moreGroups.length > 0 || moreActions.length > 0}
								<div class="my-1 h-px bg-line" role="separator" data-bar-divider-row></div>
							{/if}
							{#each dangerActions as action (action.id)}
								{@render menuRow(toRow(action), close)}
							{/each}
						{/if}
					{/snippet}
				</BarMenu>
				{#if tags && folded.has('tags')}
					<div class="absolute right-0 top-full flex w-full justify-end" aria-hidden="true">
						<TagSelector
							bind:open={tagsOpen}
							selectedTagIds={tags.selectedIds}
							tagType={tags.tagType ?? 'GENERATION'}
							allowCreate={true}
							compact={true}
							triggerStyle="none"
							panelAlign="right"
							on:change={(event) => tags?.onChange(event.detail)}
						/>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
