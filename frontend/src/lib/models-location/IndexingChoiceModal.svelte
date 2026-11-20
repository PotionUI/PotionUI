<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/stores';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, EmptyState, IconButton, Kbd, Spinner } from '$lib/components/ui';
	import { authStore } from '$lib/stores/auth';
	import { toasts } from '$lib/stores/toast';
	import { api } from '$lib/services/api';
	import { logger } from '$lib/utils/logger';
	import { formatBytes } from '$lib/utils/format';
	import { modelTypePresentation } from '$lib/utils/modelPresentation';
	import type { IndexingPendingItem } from '$lib/services/api/models';
	import IndexingFact from './IndexingFact.svelte';
	import { indexingStatusStore } from './indexingStatus.svelte';
	import {
		fileCountText,
		indexingDigests,
		isSetupPath,
		levelProfile,
		levelTimeText,
		levelLabel,
		levelUsageText,
		pickSelectionSummary,
		smallFilesText
	} from './indexingDisplay';
	import {
		emptyPickSelection,
		pickAllMatchingSelected,
		pickBytes,
		pickCount,
		pickIsApproximate,
		pickIsSelected,
		pickRequests,
		pickSelectAllMatching,
		pickToggle,
		type PickSelection
	} from './indexingPickSelection';

	const PAGE_SIZE = 50;
	const SEARCH_DEBOUNCE_MS = 250;
	const TYPE_ORDER = ['checkpoint', 'diffusion_model', 'lora', 'vae', 'text_encoder', 'controlnet', 'upscaler'];
	const PICK_SIZE_CLASS = 'md:max-w-3xl md:w-full md:h-[640px]';
	const SAVE_FAILED = 'The change could not be saved.';

	const isAdmin = $derived($authStore.user?.account_type === 'ADMIN');
	const status = $derived(isAdmin ? indexingStatusStore.status : null);
	const digests = $derived(indexingDigests(status));
	const awaiting = $derived(!!digests?.plan.awaiting_choice);
	const inSetup = $derived(isSetupPath($page.url.pathname));
	const open = $derived(isAdmin && !inSetup && !!digests && (awaiting || indexingStatusStore.pickRequested));
	const busy = $derived(indexingStatusStore.busy);
	const profile = $derived(levelProfile(digests, digests?.level ?? 'low'));

	let step = $state<'summary' | 'pick'>('summary');
	let query = $state('');
	let modelType = $state<string | null>(null);
	let items = $state<IndexingPendingItem[]>([]);
	let total = $state(0);
	let totalBytes = $state(0);
	let byType = $state<Record<string, number>>({});
	let listLoading = $state(false);
	let searchPending = $state(false);
	let moreLoading = $state(false);
	let listError = $state<string | null>(null);
	let selection = $state<PickSelection>(emptyPickSelection());
	let searchInput = $state<HTMLInputElement | null>(null);

	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	let requestSeq = 0;
	let wasOpen = false;

	const allTotal = $derived(Object.values(byType).reduce((sum, count) => sum + count, 0));
	const chips = $derived(
		Object.keys(byType)
			.filter((type) => byType[type] > 0)
			.sort((a, b) => {
				const left = TYPE_ORDER.indexOf(a);
				const right = TYPE_ORDER.indexOf(b);
				if (left !== right) return (left === -1 ? TYPE_ORDER.length : left) - (right === -1 ? TYPE_ORDER.length : right);
				return a.localeCompare(b);
			})
	);
	const filter = $derived({ query, modelType });
	const pickedCount = $derived(pickCount(selection));
	const everyMatchPicked = $derived(pickAllMatchingSelected(selection, filter, total));
	const remaining = $derived(Math.max(0, total - items.length));
	const summaryText = $derived(
		pickSelectionSummary(pickedCount, pickBytes(selection), profile, pickIsApproximate(selection))
	);
	const listSettled = $derived(!listLoading && !searchPending);
	const summaryHead = $derived(summaryText.split(' · ')[0]);
	const summaryRest = $derived(summaryText.includes(' · ') ? summaryText.slice(summaryText.indexOf(' · ')) : '');
	const title = $derived(
		step === 'pick' ? 'Pick models to index first' : `${(digests?.ready ?? 0) + (digests?.plan.files ?? 0)} models found`
	);
	const folderCount = $derived(status?.roots?.length ?? 0);
	const subtitle = $derived.by(() => {
		if (!digests) return '';
		if (step === 'pick') return `${fileCountText(digests.pending_total)} to index · ${formatBytes(digests.pending_bytes, 1)}`;
		const where = folderCount > 0 ? `In ${folderCount} ${folderCount === 1 ? 'folder' : 'folders'} · ` : '';
		return `${where}listed and ready to use now`;
	});

	$effect(() => {
		if (!isAdmin) return;
		return untrack(() => indexingStatusStore.subscribe());
	});

	$effect(() => {
		const nowOpen = open;
		if (nowOpen && !wasOpen) {
			untrack(() => {
				if (indexingStatusStore.pickRequested && !awaiting) enterPick();
				else step = 'summary';
			});
		} else if (!nowOpen && wasOpen) {
			untrack(resetPick);
		}
		wasOpen = nowOpen;
	});

	$effect(() => {
		if (step === 'pick' && searchInput) untrack(() => searchInput?.focus());
	});

	onDestroy(() => {
		if (searchTimer) clearTimeout(searchTimer);
	});

	function resetPick() {
		if (searchTimer) clearTimeout(searchTimer);
		searchPending = false;
		step = 'summary';
		query = '';
		modelType = null;
		items = [];
		total = 0;
		totalBytes = 0;
		byType = {};
		listError = null;
		selection = emptyPickSelection();
		requestSeq += 1;
	}

	function enterPick() {
		step = 'pick';
		void loadList(false);
	}

	async function loadList(append: boolean) {
		const seq = ++requestSeq;
		if (append) moreLoading = true;
		else listLoading = true;
		listError = null;
		try {
			const response = await api.listPendingIndexing({
				query: query.trim() || undefined,
				model_type: modelType ?? undefined,
				offset: append ? items.length : 0,
				limit: PAGE_SIZE
			});
			if (seq !== requestSeq) return;
			if (response.success && response.data) {
				items = append ? [...items, ...response.data.items] : response.data.items;
				total = response.data.total;
				totalBytes = response.data.total_bytes;
				byType = response.data.by_type ?? {};
			} else {
				listError = response.message ?? 'The list could not be loaded.';
			}
		} catch (e) {
			if (seq !== requestSeq) return;
			logger.error('Failed to load the files to index:', e);
			listError = 'The list could not be loaded.';
		} finally {
			if (seq === requestSeq) {
				listLoading = false;
				moreLoading = false;
			}
		}
	}

	function onSearchInput() {
		if (searchTimer) clearTimeout(searchTimer);
		searchPending = true;
		searchTimer = setTimeout(() => {
			searchPending = false;
			void loadList(false);
		}, SEARCH_DEBOUNCE_MS);
	}

	function clearSearch() {
		query = '';
		if (searchTimer) clearTimeout(searchTimer);
		searchPending = false;
		void loadList(false);
		searchInput?.focus();
	}

	function chooseType(next: string | null) {
		if (modelType === next) return;
		modelType = next;
		void loadList(false);
	}

	function selectAll() {
		selection = pickSelectAllMatching(selection, { query, modelType }, total, totalBytes);
	}

	function toggle(item: IndexingPendingItem, selected: boolean) {
		selection = pickToggle(selection, item, selected);
	}

	function failure(message: string | null, fallback: string) {
		toasts.error(message ?? fallback);
		return false;
	}

	async function indexAll(): Promise<boolean> {
		if (!(await indexingStatusStore.indexAll())) return failure(indexingStatusStore.actionError, SAVE_FAILED);
		toasts.success('Indexing started in the background');
		return true;
	}

	async function indexSelected(): Promise<boolean> {
		const count = pickedCount;
		if (count === 0) return false;
		if (!(await indexingStatusStore.indexSelected(pickRequests(selection)))) {
			return failure(indexingStatusStore.actionError, SAVE_FAILED);
		}
		toasts.success(`${count} ${count === 1 ? 'model' : 'models'} queued first`);
		selection = emptyPickSelection();
		indexingStatusStore.closePick();
		return true;
	}

	async function later() {
		if (await indexingStatusStore.later()) {
			toasts.success('Okay. You can start indexing from Admin, Models, Folders');
		} else {
			failure(indexingStatusStore.actionError, SAVE_FAILED);
		}
	}

	function back() {
		if (awaiting) step = 'summary';
		else indexingStatusStore.closePick();
	}

	function confirmChoice(): Promise<boolean> {
		return step === 'pick' ? indexSelected() : indexAll();
	}
</script>

{#snippet choice(icon: string, name: string, text: string)}
	<li class="grid grid-cols-[24px_minmax(0,1fr)] items-start gap-2">
		<Icon name={icon} className="mt-0.5 h-4 w-4 text-fg-subtle" />
		<div>
			<div class="text-base font-medium text-fg">{name}</div>
			<div class="text-base text-fg-muted">{text}</div>
		</div>
	</li>
{/snippet}

{#snippet footer()}
	{#if step === 'pick'}
		<div class="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-6 md:py-4">
			<div class="min-w-0 font-mono text-sm text-fg-muted" data-testid="pick-summary">
				<span class={pickedCount > 0 ? 'font-medium text-fg' : ''}>{summaryHead}</span>{summaryRest}
			</div>
			<div class="flex gap-3 md:ml-auto">
				<Button variant="secondary" class="max-md:min-h-11 max-md:flex-1" disabled={busy} onclick={back}>Back</Button>
				<Button
					variant="primary"
					class="max-md:min-h-11 max-md:flex-[2]"
					disabled={busy || pickedCount === 0}
					loading={busy}
					onclick={() => void confirmChoice()}
				>
					Index selected
				</Button>
			</div>
		</div>
	{:else}
		<div class="grid grid-cols-2 gap-2 px-4 py-3 md:flex md:items-center md:justify-end md:gap-3 md:px-6 md:py-4">
			<Button variant="secondary" class="max-md:order-2 max-md:min-h-11" disabled={busy} onclick={later}>Later</Button>
			<Button variant="secondary" class="max-md:order-1 max-md:min-h-11" disabled={busy} onclick={enterPick}>Pick models</Button>
			<Button
				variant="primary"
				initialFocus
				class="max-md:order-first max-md:col-span-2 max-md:min-h-11"
				disabled={busy}
				loading={busy}
				onclick={() => void confirmChoice()}
			>
				<span class="inline-flex items-center gap-2">
					Index all in the background
					<span class="max-md:hidden"><Kbd keys="Enter" /></span>
				</span>
			</Button>
		</div>
	{/if}
{/snippet}

{#if isAdmin}
	<ConfirmDialog
		isOpen={open}
		{title}
		{subtitle}
		size="md"
		sizeClass={step === 'pick' ? PICK_SIZE_CLASS : ''}
		compact={step === 'summary'}
		closeable={!awaiting}
		hideCloseButton={awaiting}
		{busy}
		resetKey={step}
		confirmLabel={step === 'pick' ? 'Index selected' : 'Index all in the background'}
		confirmDisabled={step === 'pick' && pickedCount === 0}
		onConfirm={confirmChoice}
		onCancel={() => indexingStatusStore.closePick()}
		footerContent={footer}
	>
		{#if digests && step === 'summary'}
			<div class="px-4 py-5 md:px-6">
				<p class="text-base text-fg-muted">
					Indexing reads each file once, so PotionUI can find duplicates, look your models up on CivitAI and remember
					what each one is. It runs quietly in the background and pauses while you generate.
				</p>
				<div class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
					<IndexingFact label="To index" value={fileCountText(digests.plan.files)} />
					<IndexingFact label="Total size" value={formatBytes(digests.plan.bytes, 2)} />
					<IndexingFact
						label={profile ? `Time at ${levelLabel(profile.id)}` : 'Time'}
						value={levelTimeText(digests.plan.bytes, profile)}
						wide
					/>
				</div>
				{#if digests.ready > 0}
					<p class="mt-2 text-sm text-fg-subtle">{smallFilesText(digests.ready)}</p>
				{/if}
				<ul class="mt-4 grid gap-2.5">
					{@render choice(
						'check',
						'Index all in the background',
						`${profile ? `${levelUsageText(profile)} ` : ''}Change this any time in Admin, Models, Folders.`
					)}
					{@render choice('list-checks', 'Pick models', 'Start with the ones you use. The rest wait.')}
					{@render choice('clock', 'Later', 'Nothing is read now. Start whenever you like from Admin, Models, Folders.')}
				</ul>
			</div>
		{:else if digests}
			<div class="flex h-full min-h-0 flex-col">
				<div class="space-y-3 border-b border-line px-4 py-3 md:px-6 md:pb-3 md:pt-4">
					<div class="relative">
						<Icon
							name="search"
							className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle"
						/>
						<input
							bind:this={searchInput}
							type="search"
							value={query}
							oninput={(event) => {
								query = event.currentTarget.value;
								onSearchInput();
							}}
							placeholder="Search by name"
							aria-label="Search models by name"
							autocomplete="off"
							class="input pl-9 {query ? 'pr-10' : ''} max-md:min-h-11"
						/>
						{#if query}
							<div class="absolute right-1 top-1/2 -translate-y-1/2">
								<IconButton icon="close" label="Clear search" size="sm" onclick={clearSearch} />
							</div>
						{/if}
					</div>
					<div
						class="flex flex-wrap gap-1.5 max-md:-mx-4 max-md:flex-nowrap max-md:overflow-x-auto max-md:px-4 max-md:pb-0.5 max-md:[scrollbar-width:none]"
						role="group"
						aria-label="Model type"
					>
						{#each [null, ...chips] as type (type ?? 'all')}
							{@const active = modelType === type}
							<button
								type="button"
								aria-pressed={active}
								class="inline-flex shrink-0 items-center gap-1.5 rounded border px-2.5 py-1 text-sm font-medium transition-colors max-md:min-h-9 {active
									? 'border-signal/25 bg-signal/10 text-signal'
									: 'border-line bg-surface-2 text-fg-muted hover:text-fg'}"
								onclick={() => chooseType(type)}
							>
								{type === null ? 'All' : modelTypePresentation(type).label}
								<span class="font-mono tabular-nums opacity-75">{type === null ? allTotal : byType[type]}</span>
							</button>
						{/each}
					</div>
				</div>

				<div class="flex items-center justify-between gap-3 border-b border-line px-4 py-2 text-sm text-fg-muted md:px-6">
					<span>{total === allTotal ? fileCountText(total) : `${total} of ${allTotal} match`}</span>
					<span class="flex gap-4">
						<button
							type="button"
							class="font-medium text-signal hover:underline disabled:cursor-not-allowed disabled:no-underline disabled:opacity-60 max-md:min-h-9"
							disabled={!listSettled || total === 0 || everyMatchPicked}
							onclick={selectAll}
						>
							{everyMatchPicked ? `All ${total} selected` : `Select all ${total}`}
						</button>
						{#if pickedCount > 0}
							<button
								type="button"
								class="font-medium text-signal hover:underline max-md:min-h-9"
								onclick={() => (selection = emptyPickSelection())}
							>
								Clear<span class="max-md:hidden">{' selection'}</span>
							</button>
						{/if}
					</span>
				</div>

				<ul class="min-h-0 flex-1 overflow-y-auto" aria-label="Models to index">
					{#each items as item (item.location_id)}
						{@const picked = pickIsSelected(selection, item)}
						<li>
							<label
								class="grid min-h-[52px] cursor-pointer grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-x-3 border-b border-line px-4 py-1.5 hover:bg-surface-2 md:min-h-10 md:grid-cols-[20px_minmax(0,1fr)_128px_76px] md:px-6 {picked
									? 'bg-signal/5'
									: ''}"
							>
								<input
									type="checkbox"
									class="h-4 w-4 cursor-pointer accent-signal max-md:row-span-2"
									checked={picked}
									aria-label={item.filename}
									onchange={(event) => toggle(item, event.currentTarget.checked)}
								/>
								<span class="min-w-0 truncate text-base text-fg">{item.filename}</span>
								<span class="justify-self-start max-md:col-span-2 max-md:col-start-2 max-md:row-start-2">
									<Badge
										size="lg"
										class="max-md:border-0 max-md:bg-transparent max-md:px-0 max-md:text-fg-subtle"
									>
										{modelTypePresentation(item.model_type).label}
									</Badge>
								</span>
								<span class="text-right font-mono text-sm tabular-nums text-fg-muted max-md:col-start-3 max-md:row-start-1">
									{formatBytes(item.size, 1)}
								</span>
							</label>
						</li>
					{/each}
					{#if listLoading && items.length === 0}
						<li class="flex justify-center px-6 py-8"><Spinner size="md" /></li>
					{:else if listError}
						<li class="px-6 py-8 text-center text-base text-danger">{listError}</li>
					{:else if items.length === 0}
						<li>
							<EmptyState
								compact
								icon={query.trim() ? 'search' : 'document'}
								title={query.trim() ? 'Nothing found' : 'No files waiting'}
								description={query.trim() ? `No files match "${query.trim()}".` : 'No files are waiting to be indexed.'}
							/>
						</li>
					{:else if remaining > 0}
						<li class="flex items-center justify-center gap-3 px-4 py-3 text-sm text-fg-muted md:px-6">
							<span>Showing {items.length} of {total}</span>
							<Button variant="secondary" size="sm" loading={moreLoading} class="max-md:min-h-10" onclick={() => void loadList(true)}>
								Show {Math.min(PAGE_SIZE, remaining)} more
							</Button>
						</li>
					{/if}
				</ul>
			</div>
		{/if}
	</ConfirmDialog>
{/if}
