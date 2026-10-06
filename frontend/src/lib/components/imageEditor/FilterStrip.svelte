<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button } from '$lib/components/ui';
	import {
		deleteMineFilter,
		errorText,
		filterCatalog,
		isNameTaken,
		loadCube,
		loadFilterCatalog,
		updateMineFilter
	} from '$lib/filters/catalog';
	import { selectFilterItem } from '$lib/filters/actions';
	import { buildStrip, isLocked, lockReason, moveSelection, selectableOrder } from '$lib/filters/strip';
	import { ThumbCache, renderThumb } from '$lib/filters/thumbs';
	import type { FilterItem } from '$lib/filters/types';
	import { toasts } from '$lib/stores/toast';
	import FilterMineMenu from './FilterMineMenu.svelte';
	import { PAINT_ICONS } from './icons';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;

	const THUMB_DEBOUNCE_MS = 300;

	const cache = new ThumbCache();
	let thumbs: Record<string, ImageData> = {};
	let thumbTimer: ReturnType<typeof setTimeout> | null = null;
	let thumbToken = 0;
	let firstRun = true;
	let renaming: string | null = null;
	let renameValue = '';
	let deleting: FilterItem | null = null;
	let busy = false;
	let group: HTMLDivElement;

	$: items = $filterCatalog.items;
	$: sections = buildStrip(items, $filterCatalog.groups);
	$: order = selectableOrder(sections);
	$: selectedId = state.filter.active?.id ?? null;
	$: refreshThumbs(state.contentRevision, state.activeIndex, state.ready, items);
	$: if (state.filter.error) {
		toasts.error(state.filter.error);
		session.clearFilterError();
	}

	onMount(() => {
		void loadFilterCatalog();
	});

	onDestroy(() => {
		thumbToken++;
		if (thumbTimer) clearTimeout(thumbTimer);
	});

	function refreshThumbs(revision: number, _layer: number, ready: boolean, list: FilterItem[]) {
		if (!ready || list.length === 0) return;
		if (thumbTimer) clearTimeout(thumbTimer);
		const token = ++thumbToken;
		const delay = firstRun ? 0 : THUMB_DEBOUNCE_MS;
		firstRun = false;
		thumbTimer = setTimeout(() => void renderThumbs(token, revision, list), delay);
	}

	async function renderThumbs(token: number, revision: number, list: FilterItem[]) {
		const source = session.thumbSource();
		if (!source || token !== thumbToken) return;
		cache.prune(revision);
		thumbs = { ...thumbs, none: source };
		for (const item of list) {
			if (token !== thumbToken) return;
			if (isLocked(item)) continue;
			let image = cache.get(revision, item);
			if (!image) {
				try {
					const cube = item.has_lut ? await loadCube(item) : null;
					image = renderThumb(source, item, cube);
					cache.set(revision, item, image);
				} catch {
					continue;
				}
			}
			if (token !== thumbToken) return;
			thumbs = { ...thumbs, [item.id]: image };
			await new Promise((resolve) => setTimeout(resolve, 0));
		}
	}

	function paint(node: HTMLCanvasElement, image: ImageData | undefined) {
		function draw(next: ImageData | undefined) {
			if (!next) return;
			try {
				node.getContext('2d')?.putImageData(next, 0, 0);
			} catch {
				return;
			}
		}
		draw(image);
		return { update: draw };
	}

	async function choose(item: FilterItem | null) {
		if (item && isLocked(item)) return;
		if (renaming) renaming = null;
		try {
			await selectFilterItem(session, item);
		} catch (error) {
			toasts.error(errorText(error, 'This filter could not be loaded.'));
		}
	}

	async function onKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		if (target.tagName === 'INPUT') return;
		const keys: Record<string, 'next' | 'previous' | 'first' | 'last'> = {
			ArrowRight: 'next',
			ArrowDown: 'next',
			ArrowLeft: 'previous',
			ArrowUp: 'previous',
			Home: 'first',
			End: 'last'
		};
		const move = keys[event.key];
		if (!move) return;
		event.preventDefault();
		const next = moveSelection(order, selectedId, move);
		await choose(next);
		await tick();
		group?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')?.focus();
	}

	function startRename(item: FilterItem) {
		renaming = item.id;
		renameValue = item.name;
		void tick().then(() => group?.querySelector<HTMLInputElement>('input[data-rename]')?.select());
	}

	async function commitRename(item: FilterItem) {
		if (renaming !== item.id || busy) return;
		const name = renameValue.trim().slice(0, 24);
		renaming = null;
		if (!name || name === item.name) return;
		busy = true;
		try {
			await updateMineFilter(item.id, { name });
		} catch (error) {
			toasts.error(
				isNameTaken(error) ? 'You already have a filter with that name.' : errorText(error, 'The filter could not be renamed.')
			);
		} finally {
			busy = false;
		}
	}

	async function confirmDelete() {
		const item = deleting;
		if (!item || busy) return;
		busy = true;
		try {
			await deleteMineFilter(item.id);
			if (selectedId === item.id) session.clearFilter();
			deleting = null;
		} catch (error) {
			toasts.error(errorText(error, 'The filter could not be deleted.'));
		} finally {
			busy = false;
		}
	}

	const tileSize = 'w-16 h-16 md:w-[4.5rem] md:h-[4.5rem]';
</script>

<div class="flex flex-col gap-2 min-w-0">
	{#if $filterCatalog.status === 'error'}
		<div class="flex items-center gap-2 px-3 py-2 text-xs text-danger">
			<span class="min-w-0 flex-1">{$filterCatalog.error}</span>
			<Button variant="secondary" size="xs" onclick={() => loadFilterCatalog(true)}>Retry</Button>
		</div>
	{/if}

	<div
		bind:this={group}
		role="radiogroup"
		tabindex="-1"
		aria-label="Filters"
		class="flex items-start gap-1.5 overflow-x-auto px-3 py-2"
		on:keydown={onKeydown}
	>
		<div class="shrink-0 flex flex-col items-center gap-1 w-16 md:w-[4.5rem]">
			<button
				type="button"
				role="radio"
				aria-checked={selectedId === null}
				aria-label="None"
				tabindex={selectedId === null ? 0 : -1}
				class="relative block {tileSize} overflow-hidden rounded bg-surface-2 transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal {selectedId === null ? 'ring-2 ring-signal' : ''}"
				on:click={() => choose(null)}
			>
				{#if thumbs.none}
					<canvas width={144} height={144} class="block h-full w-full" use:paint={thumbs.none}></canvas>
				{/if}
				<svg class="absolute inset-0 m-auto h-6 w-6 text-fg" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
					<circle cx="12" cy="12" r="8" stroke-width="1.8" />
					<path stroke-width="1.8" stroke-linecap="round" d="M6.5 17.5l11-11" />
				</svg>
			</button>
			<span class="text-xs {selectedId === null ? 'font-semibold text-signal' : 'text-fg-muted'}">None</span>
		</div>

		{#each sections as section (section.key)}
			<div class="shrink-0 self-stretch w-px mx-1 bg-line" aria-hidden="true"></div>
			{#each section.items as item (item.id)}
				{@const locked = isLocked(item)}
				{@const selected = selectedId === item.id}
				<div class="group shrink-0 relative flex flex-col items-center gap-1 w-16 md:w-[4.5rem]">
					<Tooltip
						text={locked ? `${item.name}: ${lockReason(item)}` : (item.description ?? item.name)}
						position="top"
						delay={250}
						wrapperClass="block"
					>
						<button
							type="button"
							role="radio"
							aria-checked={selected}
							aria-disabled={locked}
							aria-label={locked ? `${item.name}. ${lockReason(item)}` : item.name}
							tabindex={selected ? 0 : -1}
							class="relative block {tileSize} overflow-hidden rounded bg-surface-2 transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal {selected ? 'ring-2 ring-signal' : ''} {locked ? 'opacity-60 cursor-not-allowed' : ''}"
							on:click={() => choose(item)}
						>
							{#if thumbs[item.id] && !locked}
								<canvas width={144} height={144} class="block h-full w-full" use:paint={thumbs[item.id]}></canvas>
							{/if}
							{#if locked}
								<svg class="absolute left-1 top-1 h-3.5 w-3.5 text-fg-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
									<path stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d={PAINT_ICONS.lock} />
								</svg>
							{/if}
							{#if item.source === 'plugin' && !locked}
								<span class="absolute left-1 top-1"><Badge size="sm">Plugin</Badge></span>
							{/if}
							{#if item.has_lut}
								<span class="absolute bottom-1 right-1 font-mono"><Badge size="sm">LUT</Badge></span>
							{/if}
						</button>
					</Tooltip>
					{#if item.owned}
						<span class="absolute right-1 top-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100">
							<FilterMineMenu
								name={item.name}
								onRename={() => startRename(item)}
								onDelete={() => (deleting = item)}
							/>
						</span>
					{/if}
					{#if renaming === item.id}
						<input
							data-rename
							aria-label="Rename {item.name}"
							maxlength="24"
							class="w-full rounded border border-line-strong bg-surface-2 px-1 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-signal"
							bind:value={renameValue}
							on:keydown|stopPropagation={(event) => {
								if (event.key === 'Enter') void commitRename(item);
								else if (event.key === 'Escape') renaming = null;
							}}
							on:blur={() => commitRename(item)}
						/>
					{:else}
						<span
							class="w-full truncate text-center text-xs {selected ? 'font-semibold text-signal' : locked ? 'text-fg-subtle' : 'text-fg-muted'}"
						>
							{item.name}
						</span>
					{/if}
				</div>
			{/each}
		{/each}

		{#if $filterCatalog.status === 'loading' && items.length === 0}
			{#each Array(6) as _, index (index)}
				<div class="shrink-0 {tileSize} rounded bg-surface-2" aria-hidden="true"></div>
			{/each}
		{/if}
	</div>

	{#if deleting}
		<div
			role="alertdialog"
			aria-label="Delete filter"
			class="mx-3 mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-line-strong bg-surface-2 px-3 py-2"
		>
			<p class="min-w-0 flex-1 text-sm text-fg">Delete {deleting.name}? This can't be undone.</p>
			<Button variant="ghost" size="sm" disabled={busy} onclick={() => (deleting = null)}>Cancel</Button>
			<Button variant="danger" size="sm" disabled={busy} onclick={confirmDelete}>Delete</Button>
		</div>
	{/if}
</div>
