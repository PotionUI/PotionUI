<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { tagCatalog } from '$lib/stores/tagCatalog';
	import { historyCollectionsStore, libraryCollectionsStore } from '$lib/stores/collections';
	import { historyTileSize } from '$lib/stores/historyTileSize';
	import { LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import { isTaken } from '$lib/kits/kitCounts';
	import type { Kit, KitDetail, KitItem, KitItemKind, KitLabelCount } from '$lib/kits/types';
	import type { LibraryItem } from '$lib/services/api/library';
	import type { GenerationHistoryItem, HistoryFacets, Tag } from '$lib/types/history';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, EmptyState, IconButton, LoadErrorState, SegmentedControl, Spinner } from '$lib/components/ui';
	import BaseModal from '../BaseModal.svelte';
	import {
		createMediaSelection,
		isMediaTypeSelectable,
		mediaTypeConstraintMessage,
		type PickedMedia,
		type PickerMediaType
	} from '../mediaPickerSelection';
	import PickerToolbar from './PickerToolbar.svelte';
	import PickerFooter from './PickerFooter.svelte';
	import PickerTile from './PickerTile.svelte';
	import PickerKitCard from './PickerKitCard.svelte';
	import TileSizeControl from './TileSizeControl.svelte';
	import {
		DEFAULT_PAGE_SIZE,
		KIT_PAGE_SIZE,
		PICKER_TILE_SCALE,
		SOURCE_META,
		activeFilterCount,
		clearSource,
		freshFilters,
		pagesFor,
		popoverFilterCount,
		rangeText,
		tierForWidth,
		toHistoryRequest,
		toKitRequest,
		toLibraryRequest,
		type PickerFilters,
		type PickerSourceId
	} from './pickerModel';
	import {
		entryFromKitItem,
		historyEntries,
		libraryEntries,
		type PickerEntry
	} from './pickerEntries';
	import {
		isAtLimit,
		itemDim,
		kitCardDim,
		nextFocusIndex,
		selectedBySource,
		selectedCountText,
		splitKeys,
		toggleWithLimit,
		usedCount,
		wholeKitBlocked,
		wholeKitKey,
		wholeKitLabel,
		acceptedOnlyReason,
		type ArrowKey
	} from './pickerSelection';
	import type { MediaPickerKits, PickerConfirmLabel } from './types';
	import { rememberKit } from '$lib/stores/kits';

	type Status = 'loading' | 'ready' | 'error';

	const ALL_KINDS: PickerMediaType[] = ['image', 'video', 'audio', 'mesh'];
	const NOUNS: Record<PickerMediaType, string> = { image: 'image', video: 'video', audio: 'audio', mesh: '3D file' };
	const ARROWS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);
	const EMPTY_FACETS: HistoryFacets = { modes: [], presets: [], models: [] };

	let {
		isOpen = false,
		onClose,
		title,
		sources = ['history', 'library'],
		initialSource,
		mediaType,
		mediaTypes,
		selectableMediaTypes,
		multiple = false,
		maxCount = null,
		selectedKeys,
		onSelectionChange,
		onConfirm,
		onPick,
		confirmLabel = 'Use selected',
		closeOnPick = true,
		kitItems = true,
		kits
	}: {
		isOpen?: boolean;
		onClose: () => void;
		title?: string;
		sources?: PickerSourceId[];
		initialSource?: PickerSourceId;
		mediaType?: PickerMediaType;
		mediaTypes?: PickerMediaType[];
		selectableMediaTypes?: PickerMediaType[];
		multiple?: boolean;
		maxCount?: number | null;
		selectedKeys?: string[];
		onSelectionChange?: (keys: string[], items: PickedMedia[]) => void;
		onConfirm?: (keys: string[], items: PickedMedia[]) => void;
		onPick?: (picked: PickedMedia) => void;
		confirmLabel?: PickerConfirmLabel;
		closeOnPick?: boolean;
		kitItems?: boolean;
		kits?: MediaPickerKits;
	} = $props();

	const uid = $props.id();
	const titleId = `${uid}-title`;
	const picker = createMediaSelection('any');
	const selection = picker.selection;

	let kinds = $derived<PickerMediaType[]>(mediaTypes?.length ? mediaTypes : mediaType ? [mediaType] : ALL_KINDS);
	let constrained = $derived<readonly PickerMediaType[] | undefined>(selectableMediaTypes?.length ? selectableMediaTypes : undefined);
	let acceptedKinds = $derived<readonly PickerMediaType[]>(constrained ?? kinds);
	let reasonText = $derived(constrained ? mediaTypeConstraintMessage(constrained) : acceptedOnlyReason(kinds));
	let maxItems = $derived(maxCount ?? kits?.mediaRoom ?? null);
	let wholeKit = $derived(multiple && !!kits?.onLoad);
	let itemsPickable = $derived(kitItems);
	let heading = $derived(title ?? `Choose ${kinds.length === 1 ? NOUNS[kinds[0]] : 'media'}`);

	function kindsFor(src: PickerSourceId): PickerMediaType[] {
		return src === 'history' ? kinds : kinds.filter((kind) => kind !== 'mesh');
	}

	function kitAccepts(kind: KitItemKind): boolean {
		if (kits?.accepted !== undefined) return isTaken(kind, kits.accepted);
		return kind !== 'text' && acceptedKinds.includes(kind);
	}

	let width = $state(typeof window === 'undefined' ? 1280 : window.innerWidth);
	let tier = $derived(tierForWidth(width));

	let source = $state<PickerSourceId>('history');
	let filters = $state<PickerFilters>(freshFilters());
	let applied = $state({ history: '', library: '', kits: '' });
	let page = $state({ history: 1, library: 1, kits: 1 });
	let pageSize = $state(DEFAULT_PAGE_SIZE);
	let reloadTick = $state(0);
	let notice = $state('');
	let busy = $state(false);
	let justLoaded = $state<string[]>([]);

	let historyView = $state({ generations: [] as GenerationHistoryItem[], total: 0, status: 'loading' as Status, loaded: false });
	let libraryView = $state({ items: [] as LibraryItem[], total: 0, status: 'loading' as Status, loaded: false });
	let kitsView = $state({
		kits: [] as Kit[],
		total: 0,
		labels: [] as KitLabelCount[],
		status: 'loading' as Status,
		loaded: false
	});
	let directory = $state<Record<string, Kit>>({});
	let openKit = $state<KitDetail | null>(null);
	let openingKit = $state(false);
	let facets = $state<HistoryFacets>(EMPTY_FACETS);
	let tagLists = $state<{ history: Tag[]; library: Tag[] }>({ history: [], library: [] });
	let gridEl = $state<HTMLDivElement>();

	const seq: Record<PickerSourceId, number> = { history: 0, library: 0, kits: 0 };
	const lastKey: Record<PickerSourceId, string> = { history: '', library: '', kits: '' };
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	let wasOpen = false;
	let facetsLoaded = false;

	let status = $derived(source === 'history' ? historyView.status : source === 'library' ? libraryView.status : kitsView.status);
	let loaded = $derived(source === 'history' ? historyView.loaded : source === 'library' ? libraryView.loaded : kitsView.loaded);
	let sourceTotal = $derived(source === 'history' ? historyView.total : source === 'library' ? libraryView.total : kitsView.total);
	let entries = $derived<PickerEntry[]>(
		source === 'history'
			? historyEntries(historyView.generations, kindsFor('history'))
			: source === 'library'
				? libraryEntries(libraryView.items, kindsFor('library'))
				: []
	);
	let kitEntries = $derived<PickerEntry[]>(openKit ? openKit.items.map((item: KitItem) => entryFromKitItem(openKit as KitDetail, item)) : []);
	let kitLabels = $derived(kitsView.labels.map((entry) => entry.label));
	let toolbarKinds = $derived(kindsFor(source));
	let activeCount = $derived(activeFilterCount(source, filters, toolbarKinds));
	let popoverCount = $derived(popoverFilterCount(source, filters, tier, toolbarKinds));
	let collections = $derived(source === 'library' ? $libraryCollectionsStore.collections : $historyCollectionsStore.collections);
	let collectionsLoading = $derived(source === 'library' ? $libraryCollectionsStore.loading : $historyCollectionsStore.loading);
	let tags = $derived(source === 'library' ? tagLists.library : tagLists.history);

	let effectivePageSize = $derived(source === 'kits' ? KIT_PAGE_SIZE : pageSize);
	let currentPage = $derived(page[source]);
	let totalPages = $derived(pagesFor(sourceTotal, effectivePageSize));
	let inKit = $derived(source === 'kits' && openKit !== null);

	function fitOf(kit: Pick<Kit, 'counts'> | undefined): number {
		if (!kit) return 0;
		let fit = 0;
		for (const [kind, count] of Object.entries(kit.counts) as Array<[KitItemKind, number]>) {
			if (kitAccepts(kind)) fit += count;
		}
		return fit;
	}

	const fitById = (id: string) => fitOf(directory[id]);

	let used = $derived(usedCount($selection, fitById));
	let atLimit = $derived(multiple && isAtLimit(maxItems, used));
	let perSource = $derived(selectedBySource($selection));
	let tabItems = $derived(
		sources.map((id) => ({
			id,
			label: SOURCE_META[id].label,
			icon: SOURCE_META[id].icon,
			...(multiple && perSource[id] > 0 ? { count: perSource[id] } : {})
		}))
	);
	let tileScale = $derived(PICKER_TILE_SCALE[$historyTileSize]);

	let badgeText = $derived(
		inKit
			? `${kitEntries.length} items`
			: sourceTotal > 0 && status !== 'error'
				? `${sourceTotal} ${SOURCE_META[source].noun}`
				: ''
	);

	$effect(() => {
		picker.follow(selectedKeys);
	});

	$effect(() => {
		picker.track(isOpen);
	});

	$effect(() => {
		const open = isOpen;
		if (open === wasOpen) return;
		wasOpen = open;
		if (open) untrack(resetForOpen);
	});

	function resetForOpen() {
		filters = freshFilters();
		applied = { history: '', library: '', kits: '' };
		page = { history: 1, library: 1, kits: 1 };
		openKit = null;
		notice = '';
		busy = false;
		justLoaded = [];
		lastKey.history = lastKey.library = lastKey.kits = '';
		source = initialSource && sources.includes(initialSource) ? initialSource : (sources[0] ?? 'history');
		void loadTags();
		void primeFacets();
	}

	async function loadTags() {
		try {
			if (sources.includes('history')) tagLists.history = await tagCatalog.get<Tag>('GENERATION', { fresh: true });
			if (sources.includes('library')) tagLists.library = await tagCatalog.get<Tag>(LIBRARY_TAG_TYPE, { fresh: true });
		} catch (error) {
			logger.error('Failed to load picker tags:', error);
		}
	}

	async function primeFacets() {
		if (facetsLoaded || !sources.includes('history')) return;
		facetsLoaded = true;
		try {
			const response = await api.getHistoryFacets();
			if (response.success && response.data) facets = response.data;
		} catch (error) {
			facetsLoaded = false;
			logger.error('Failed to load history facets:', error);
		}
	}

	$effect(() => {
		if (!isOpen) return;
		const src = source;
		const draft = { ...filters[src], search: applied[src] };
		const current = page[src];
		const size = pageSize;
		const kindList = kindsFor(src);
		reloadTick;
		const request =
			src === 'history'
				? toHistoryRequest(draft as PickerFilters['history'], { kinds: kindList, page: current, pageSize: size })
				: src === 'library'
					? toLibraryRequest(draft as PickerFilters['library'], { kinds: kindList, page: current, pageSize: size })
					: toKitRequest(draft as PickerFilters['kits'], current);
		const key = JSON.stringify([src, request, reloadTick]);
		untrack(() => {
			if (lastKey[src] === key) return;
			lastKey[src] = key;
			void load(src, request);
		});
	});

	$effect(() => {
		if (!isOpen) return;
		const src = source;
		untrack(() => {
			if (src === 'history') void historyCollectionsStore.load();
			if (src === 'library') void libraryCollectionsStore.load();
		});
	});

	async function load(src: PickerSourceId, request: unknown) {
		const mine = ++seq[src];
		if (src === 'history') historyView.status = 'loading';
		else if (src === 'library') libraryView.status = 'loading';
		else kitsView.status = 'loading';
		try {
			if (src === 'history') {
				const response = await api.getGenerationHistory(request as never);
				if (mine !== seq[src]) return;
				if (!response.success || !response.data) throw new Error('history');
				historyView.generations = response.data.generations ?? [];
				historyView.total = response.data.total ?? 0;
				historyView.loaded = true;
				historyView.status = 'ready';
				picker.remember(historyEntries(historyView.generations, kindsFor('history')).map((entry) => entry.picked));
			} else if (src === 'library') {
				const response = await api.listLibraryItems(request as never);
				if (mine !== seq[src]) return;
				if (!response.success || !response.data) throw new Error('library');
				libraryView.items = response.data.items ?? [];
				libraryView.total = response.data.total ?? 0;
				libraryView.loaded = true;
				libraryView.status = 'ready';
				picker.remember(libraryEntries(libraryView.items, kindsFor('library')).map((entry) => entry.picked));
			} else {
				const response = await api.listKits(request as never);
				if (mine !== seq[src]) return;
				if (!response.success || !response.data) throw new Error('kits');
				kitsView.kits = response.data.kits;
				kitsView.total = response.data.total;
				kitsView.labels = response.data.labels;
				kitsView.loaded = true;
				kitsView.status = 'ready';
				for (const kit of response.data.kits) directory[kit.id] = kit;
			}
		} catch (error) {
			if (mine !== seq[src]) return;
			logger.error(`Failed to load ${src} for the media picker:`, error);
			lastKey[src] = '';
			if (src === 'history') historyView.status = 'error';
			else if (src === 'library') libraryView.status = 'error';
			else kitsView.status = 'error';
		}
	}

	onDestroy(() => clearTimeout(searchTimer));

	function changeFilters(next: PickerFilters) {
		const src = source;
		const before = filters[src].search;
		const after = next[src].search;
		filters = next;
		page[src] = 1;
		if (before !== after) {
			clearTimeout(searchTimer);
			searchTimer = setTimeout(() => {
				applied[src] = filters[src].search;
			}, 300);
		} else {
			clearTimeout(searchTimer);
			applied[src] = after;
		}
	}

	function clearFilters() {
		clearTimeout(searchTimer);
		filters = clearSource(source, filters);
		applied[source] = '';
		page[source] = 1;
	}

	function selectSource(id: string) {
		source = id as PickerSourceId;
		openKit = null;
	}

	function setPage(next: number) {
		page[source] = next;
	}

	function setPageSize(size: number) {
		pageSize = size;
		page = { history: 1, library: 1, kits: 1 };
	}

	function emitToggle(key: string) {
		const next = toggleWithLimit($selection, key, maxItems, fitById);
		picker.set(next, onSelectionChange);
	}

	function handlePick(entry: PickerEntry) {
		onPick?.(entry.picked);
		if (closeOnPick) onClose();
	}

	function handleCancel() {
		if (multiple) picker.cancel(onSelectionChange);
		onClose();
	}

	function whyFailed(error: unknown): string {
		const status = (error as { response?: { status?: number } } | null)?.response?.status;
		return status === 404 ? 'no longer exists' : 'could not be reached';
	}

	async function confirm() {
		const keys = get(selection);
		if (keys.length === 0 || busy) return;
		const { itemKeys, kitIds } = splitKeys(keys);
		busy = true;
		notice = '';
		const loaded: KitDetail[] = [];
		const failures: string[] = [];
		const failedIds: string[] = [];
		try {
			for (const id of kitIds) {
				const name = directory[id]?.name ?? id;
				try {
					const response = await api.getKit(id);
					if (response.success && response.data) {
						rememberKit(response.data);
						loaded.push(response.data);
					} else {
						failures.push(`${name} could not be loaded`);
						failedIds.push(id);
					}
				} catch (error) {
					failures.push(`${name} ${whyFailed(error)}`);
					failedIds.push(id);
				}
			}
			if (itemKeys.length > 0) onConfirm?.(itemKeys, picker.resolve(itemKeys));
			if (loaded.length > 0) kits?.onLoad?.(loaded);
			if (failures.length === 0) {
				onClose();
				return;
			}
			justLoaded = [...justLoaded, ...loaded.map((kit) => kit.id)];
			picker.set(failedIds.map(wholeKitKey), undefined);
			notice = `${loaded.length > 0 ? 'Some kits were loaded. ' : ''}Could not load: ${failures.join('; ')}.`;
		} catch (error) {
			logger.error('Failed to confirm the media picker:', error);
			notice = 'Could not load the selected kit. Try again.';
		} finally {
			busy = false;
		}
	}

	async function openKitCard(kit: Kit) {
		if (openingKit) return;
		openingKit = true;
		notice = '';
		try {
			const response = await api.getKit(kit.id);
			if (response.success && response.data) {
				openKit = response.data;
				directory[kit.id] = response.data;
				picker.remember(response.data.items.map((item: KitItem) => entryFromKitItem(response.data as KitDetail, item).picked));
			} else {
				notice = `Could not open ${kit.name}.`;
			}
		} catch (error) {
			logger.error('Failed to open kit:', error);
			notice = `Could not open ${kit.name}.`;
		} finally {
			openingKit = false;
		}
	}

	async function useWholeKit() {
		if (!openKit) return;
		const key = wholeKitKey(openKit.id);
		if (!$selection.includes(key)) {
			picker.set(toggleWithLimit($selection, key, maxItems, fitById), onSelectionChange);
		}
		await confirm();
	}

	function dimFor(entry: PickerEntry, fromKit: boolean) {
		const selected = $selection.includes(entry.key);
		const allowed = fromKit
			? kitAccepts(entry.kind as KitItemKind)
			: !constrained || isMediaTypeSelectable(entry.kind, constrained);
		const dim = itemDim({ allowed, acceptedKinds, selected, multiple, max: maxItems, used });
		return dim.reason && !allowed ? { ...dim, reason: reasonText } : dim;
	}

	function kitDim(kit: Kit) {
		const key = wholeKitKey(kit.id);
		const loadedAlready = [...(kits?.loadedIds ?? []), ...justLoaded].includes(kit.id);
		if (loadedAlready) return { disabled: true, reason: 'Already loaded' };
		return kitCardDim({
			fit: fitOf(kit),
			selected: $selection.includes(key),
			multiple: wholeKit,
			max: maxItems,
			used,
			acceptedKinds
		});
	}

	function handleGridKeydown(event: KeyboardEvent) {
		if (!ARROWS.has(event.key) || !gridEl) return;
		const target = event.target as HTMLElement;
		if (!target.matches('[data-pick-focus]')) return;
		event.preventDefault();
		const nodes = Array.from(gridEl.querySelectorAll<HTMLElement>('[data-pick-focus]')).filter(
			(node) => !(node as HTMLButtonElement).disabled
		);
		const rects = nodes.map((node) => {
			const box = (node.closest('[data-pick-tile], [data-pick-kit]') ?? node).getBoundingClientRect();
			return { left: box.left, top: box.top, width: box.width, height: box.height };
		});
		const index = nextFocusIndex(rects, nodes.indexOf(target), event.key as ArrowKey);
		if (index >= 0 && nodes[index] && nodes[index] !== target) {
			nodes[index].focus();
			nodes[index].scrollIntoView?.({ block: 'nearest' });
		}
	}

	let emptyCopy = $derived(
		source === 'history'
			? 'Generations you make show up here.'
			: source === 'library'
				? 'Upload files or take them from History and they show up here.'
				: 'Make a kit in the Library and it shows up here.'
	);
	let confirmText = $derived(
		typeof confirmLabel === 'function'
			? confirmLabel({
					count: used,
					kitNames: splitKeys($selection).kitIds.map((id) => directory[id]?.name ?? 'kit')
				})
			: confirmLabel
	);
	let rangeLabel = $derived(
		inKit
			? `${kitEntries.length} items`
			: source === 'kits'
				? sourceTotal > 0
					? `${sourceTotal} kits`
					: ''
				: rangeText(currentPage, effectivePageSize, sourceTotal)
	);
	let kitFitOpen = $derived(openKit ? fitOf(openKit) : 0);
	let wholeKitOver = $derived(openKit ? wholeKitBlocked(kitFitOpen, maxItems, used) : false);
</script>

<svelte:window bind:innerWidth={width} />

<BaseModal
	{isOpen}
	hideCloseButton
	labelledBy={titleId}
	sizeClass="md:max-w-6xl md:w-full md:h-[min(860px,90vh)]"
	on:close={handleCancel}
>
	<svelte:fragment slot="header">
		<div class="flex w-full flex-wrap items-center gap-x-4 gap-y-2" data-media-picker data-source={source}>
			<div class="flex min-h-10 min-w-0 flex-1 items-center gap-3 md:flex-none">
				<h2 id={titleId} class="truncate text-lg font-semibold text-fg">{heading}</h2>
			</div>
			{#if sources.length > 1}
				<div class="w-full md:w-auto max-md:order-3" data-picker-tabs>
					<SegmentedControl items={tabItems} selected={source} onSelect={selectSource} ariaLabel="Source" />
				</div>
			{/if}
			<div class="ml-auto flex items-center gap-2 max-md:order-2">
				{#if badgeText}
					<Badge variant="neutral" class="font-mono tabular-nums">{badgeText}</Badge>
				{/if}
				<IconButton icon="close" label="Close" size="md" onclick={handleCancel} />
			</div>
			<div class="order-last w-full">
				{#if inKit && openKit}
					<div class="flex flex-wrap items-center gap-x-3 gap-y-2" data-kit-bar>
						<div class="flex min-w-0 flex-1 basis-[10rem] items-center gap-2">
							<Button variant="ghost" size="sm" icon="chevron-left" class="!h-8 flex-shrink-0 !py-0" onclick={() => (openKit = null)}>
								All kits
							</Button>
							<h3 class="min-w-0 truncate text-md font-semibold text-fg">{openKit.name}</h3>
							{#if openKit.label}<span class="flex-shrink-0"><Badge size="sm">{openKit.label}</Badge></span>{/if}
							<span class="hidden font-mono text-sm text-fg-subtle lg:inline">@{openKit.handle}</span>
						</div>
						{#if wholeKit}
							<Button
								variant="secondary"
								size="sm"
								icon="layers"
								class="!h-8 !py-0"
								disabled={wholeKitOver || busy}
								attrs={{ 'data-use-whole-kit': '' }}
								onclick={useWholeKit}
							>
								Use whole kit
								<span class="font-mono tabular-nums {wholeKitOver && kitFitOpen > 0 ? 'text-warning' : 'text-fg-muted'}">
									{wholeKitLabel(kitFitOpen, acceptedKinds)}
								</span>
							</Button>
						{/if}
						<TileSizeControl />
					</div>
				{:else}
					<PickerToolbar
						{source}
						{filters}
						kinds={toolbarKinds}
						{tier}
						{tags}
						{collections}
						{collectionsLoading}
						{facets}
						{kitLabels}
						{activeCount}
						{popoverCount}
						onChange={changeFilters}
						onClear={clearFilters}
					/>
				{/if}
			</div>
		</div>
	</svelte:fragment>

	<div class="pk-grid relative min-h-full" style="--pk-m:{tileScale}" data-picker-body aria-busy={status === 'loading'}>
		{#if notice}
			<div class="px-4 pt-4 md:px-5">
				<p class="flex items-center gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger" role="alert" data-picker-notice data-kit-load-problems>
					<Icon name="warning" className="h-4 w-4 flex-shrink-0" />{notice}
				</p>
			</div>
		{/if}

		{#if inKit && openKit}
			{#if kitEntries.length === 0}
				<div class="flex h-full items-center justify-center p-5">
					<EmptyState title="This kit is empty" description="Add items to it in the Library." compact />
				</div>
			{:else}
				<div class="p-4 md:p-5" bind:this={gridEl} onkeydown={handleGridKeydown} role="presentation">
					<div class="pk-row flex flex-wrap gap-3" data-picker-grid>
						{#each kitEntries as entry (entry.key)}
							{@const dim = dimFor(entry, true)}
							<PickerTile
								{entry}
								{multiple}
								readonly={!itemsPickable}
								selected={$selection.includes(entry.key)}
								disabled={itemsPickable && dim.disabled}
								reason={itemsPickable ? dim.reason : ''}
								onActivate={handlePick}
								onToggle={(tile) => emitToggle(tile.key)}
							/>
						{/each}
					</div>
				</div>
			{/if}
		{:else if status === 'error'}
			<LoadErrorState
				title="Could not load {SOURCE_META[source].label}"
				message="The server did not answer. Your selection is kept."
				onRetry={() => reloadTick++}
			/>
		{:else if status === 'loading' && !loaded}
			<div class="flex h-full min-h-[16rem] flex-col items-center justify-center gap-3 py-12" role="status" data-picker-loading>
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted">Loading…</p>
			</div>
		{:else if (source === 'kits' ? kitsView.kits.length : entries.length) === 0 && status === 'ready'}
			<div class="flex h-full items-center justify-center p-5">
				{#if activeCount > 0}
					<EmptyState
						icon="search"
						title="No matches"
						description="Nothing in {SOURCE_META[source].label} fits these filters."
						compact
					>
						{#snippet actions()}
							<Button size="sm" variant="secondary" icon="close" onclick={clearFilters}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<EmptyState title="Nothing here yet" description={emptyCopy} compact />
				{/if}
			</div>
		{:else}
			{#if status === 'loading'}
				<div class="pointer-events-none sticky top-0 z-20 flex h-0 justify-center" role="status" data-picker-loading>
					<div class="mt-16 flex items-center gap-2.5 rounded-lg border border-line-strong bg-surface-2 px-4 py-3 text-fg-muted shadow-floating">
						<Spinner size="md" /><span class="text-sm">Loading…</span>
					</div>
				</div>
			{/if}
			<div class="p-4 md:p-5 {status === 'loading' ? 'pointer-events-none opacity-40' : ''}" bind:this={gridEl} onkeydown={handleGridKeydown} role="presentation">
				<div class="pk-row flex flex-wrap gap-3" data-picker-grid>
					{#if source === 'kits'}
						{#each kitsView.kits as kit (kit.id)}
							{@const dim = kitDim(kit)}
							<PickerKitCard
								{kit}
								multiple={wholeKit}
								selected={$selection.includes(wholeKitKey(kit.id))}
								disabled={dim.disabled}
								openDisabled={fitOf(kit) === 0}
								reason={dim.reason}
								accepted={kitAccepts}
								onOpen={openKitCard}
								onToggle={(card) => emitToggle(wholeKitKey(card.id))}
							/>
						{/each}
					{:else}
						{#each entries as entry (entry.key)}
							{@const dim = dimFor(entry, false)}
							<PickerTile
								{entry}
								{multiple}
								selected={$selection.includes(entry.key)}
								disabled={dim.disabled}
								reason={dim.reason}
								onActivate={handlePick}
								onToggle={(tile) => emitToggle(tile.key)}
							/>
						{/each}
					{/if}
				</div>
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<PickerFooter
			{multiple}
			selectedText={selectedCountText(used, maxItems)}
			{atLimit}
			rangeText={rangeLabel}
			paged={!inKit}
			page={currentPage}
			pages={totalPages}
			pageSize={effectivePageSize}
			showPageSize={source !== 'kits'}
			confirmLabel={confirmText}
			confirmDisabled={used === 0 || busy}
			onPage={setPage}
			onPageSize={setPageSize}
			onCancel={handleCancel}
			onConfirm={confirm}
		/>
	</svelte:fragment>
</BaseModal>

<style>
	.pk-grid {
		--pk-rh: calc(128px * var(--pk-m, 1));
		--pk-min: 96px;
	}
	@media (max-width: 767px) {
		.pk-grid {
			--pk-rh: calc(92px * var(--pk-m, 1));
			--pk-min: 70px;
		}
	}
	.pk-row::after {
		content: '';
		flex: 9999 1 0;
	}
</style>
