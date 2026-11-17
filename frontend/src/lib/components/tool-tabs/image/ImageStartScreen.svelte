<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Card, EmptyState, IconButton, Input, Kbd, LoadErrorState, PageTitle, Pagination, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { EditSummary } from '$lib/services/api/edits';
	import { confirmDialog } from '$lib/stores/confirm';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import {
		createEditPictures,
		createRecentEdits,
		editLabel,
		fileStem,
		loadHistoryPicks,
		openEditTabs,
		pageCount,
		pictureKey,
		relativeTime,
		sourceLook,
		type ContinueResult,
		type HistoryPick
	} from '$lib/tool-tabs/image/recentEdits';
	import { sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import type { MediaRef } from '$lib/types/tabs';
	import EditTile from './EditTile.svelte';
	import ImageSourcePicker from './ImageSourcePicker.svelte';

	let {
		tabId,
		phone = false,
		busy = false,
		error = null,
		onpick,
		onfile
	}: {
		tabId: string;
		phone?: boolean;
		busy?: boolean;
		error?: string | null;
		onpick: (source: MediaRef) => void;
		onfile: (file: File) => void;
	} = $props();

	const recent = createRecentEdits({
		edits: api,
		library: api,
		tabId: untrack(() => tabId),
		openTabs: () => get(tabsStore).tabs,
		focusTab: (id) => tabsStore.setActiveTab(id),
		restoreTab: (patch) => tabsStore.restoreToolTab(untrack(() => tabId), patch)
	});
	const pictures = createEditPictures(api);

	const ENTRY_ORDER: SourceEntryId[] = ['browse', 'history', 'library'];
	const ENTRY_COPY: Record<string, { label: string; hint: string } | undefined> = {
		browse: { label: 'Upload file', hint: '' },
		history: { label: 'History', hint: 'Pictures you made' },
		library: { label: 'Library', hint: 'Pictures you saved' },
		kits: { label: 'Kits', hint: 'Pictures in your kits' }
	};

	let picker = $state<ReturnType<typeof ImageSourcePicker>>();
	let dragging = $state(false);
	let urls = $state<Record<string, string | null>>({});
	let history = $state<HistoryPick[]>([]);
	let historyLoaded = $state(false);
	let historyRequested = false;
	const requested = new Set<string>();

	let entries = $derived.by(() => {
		const all = sourceEntries('image', phone);
		const first = ENTRY_ORDER.flatMap((id) => all.filter((entry) => entry.id === id));
		const rest = all.filter((entry) => !ENTRY_ORDER.includes(entry.id));
		return [...first, ...rest].map((entry) => {
			const copy = ENTRY_COPY[entry.id];
			const hint = entry.id === 'browse' ? (phone ? 'From this phone' : 'From this computer') : (copy?.hint ?? '');
			return { ...entry, label: copy?.label ?? entry.label, hint };
		});
	});
	let openMap = $derived(openEditTabs($tabsStore.tabs, tabId));
	let firstUse = $derived($recent.status === 'ready' && $recent.overall === 0 && !$recent.query);
	let emptyCompact = $derived(!historyLoaded || history.length > 0);
	let listOff = $derived($recent.status !== 'ready');
	let totalPages = $derived(pageCount($recent.total));
	let countLabel = $derived(
		$recent.status !== 'ready' || firstUse ? undefined : $recent.query ? `${$recent.total} of ${$recent.overall}` : $recent.overall
	);

	onMount(() => {
		void recent.load();
		return () => recent.destroy();
	});

	$effect(() => {
		for (const edit of $recent.edits) {
			const key = pictureKey(edit);
			if (requested.has(key)) continue;
			requested.add(key);
			void pictures.resolve(edit).then((url) => {
				urls[key] = url;
			});
		}
	});

	$effect(() => {
		if (!firstUse || historyRequested) return;
		historyRequested = true;
		void loadHistoryPicks(api, 6).then((picks) => {
			history = picks;
			historyLoaded = true;
		});
	});

	function leave(event: DragEvent) {
		const next = event.relatedTarget;
		if (next instanceof Node && (event.currentTarget as HTMLElement).contains(next)) return;
		dragging = false;
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		const file = Array.from(event.dataTransfer?.files ?? []).find((candidate) => candidate.type.startsWith('image/'));
		if (file) onfile(file);
	}

	function explain(result: ContinueResult) {
		if (result.kind === 'missing') toasts.error('That edit no longer exists.');
		else if (result.kind === 'source-missing') toasts.error('The picture for this edit is no longer available.');
		else if (result.kind === 'failed') toasts.error('Could not open this edit. Try again.');
	}

	async function open(edit: EditSummary) {
		explain(await recent.continueEdit(edit.id));
	}

	async function rename(edit: EditSummary, draft: string): Promise<boolean> {
		const result = await recent.rename(edit.id, draft);
		if (result.kind === 'failed') {
			toasts.error('Could not rename this edit. Try again.');
			return false;
		}
		if (result.kind === 'missing') toasts.error('That edit no longer exists.');
		return true;
	}

	async function duplicate(edit: EditSummary) {
		const result = await recent.duplicate(edit.id);
		if (result.kind === 'done') toasts.success('Edit duplicated.');
		else if (result.kind === 'missing') toasts.error('That edit no longer exists.');
		else toasts.error('Could not duplicate this edit. Try again.');
	}

	async function remove(edit: EditSummary) {
		const confirmed = await confirmDialog({
			title: 'Delete this edit?',
			message: `“${editLabel(edit).text}” and its changes will be removed. The original picture is not affected.`,
			variant: 'danger',
			confirmLabel: 'Delete edit'
		});
		if (!confirmed) return;
		const result = await recent.remove(edit.id);
		if (result.kind === 'failed') toasts.error('Could not delete this edit. Try again.');
	}
</script>

<div
	class="relative flex min-h-0 min-w-0 flex-1 flex-col"
	role="region"
	aria-label="Open a picture"
	ondragover={(event) => {
		event.preventDefault();
		dragging = true;
	}}
	ondragleave={leave}
	ondrop={drop}
	data-image-empty
	data-testid="tool-tab-empty"
	data-dragging={dragging ? '' : undefined}
>
	<div class="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden bg-canvas p-6 dot-grid max-md:p-3">
		<p class="sr-only">Open a picture to edit, or continue one of your recent edits.</p>
		<div class="start flex min-h-full min-w-0 flex-col gap-6 max-md:gap-4" data-start-screen>
			<section class="band" aria-label="Start from a picture" data-start-panel>
				<Card>
					<div class="mb-3 flex min-h-5 items-baseline gap-3">
						<h2 class="whitespace-nowrap text-base font-semibold">Start from a picture</h2>
						<p class="band-sub whitespace-nowrap text-sm text-fg-muted">Open one to begin a new edit.</p>
						<div class="band-hint ml-auto inline-flex items-center gap-2 whitespace-nowrap text-sm {dragging ? 'text-signal' : 'text-fg-subtle'}" data-start-hint>
							{#if dragging}
								<span>Release to start an edit</span>
							{:else}
								<span>Drop a picture anywhere{phone ? '' : ', or paste'}</span>
								{#if !phone}<span class="inline-flex items-center gap-1 whitespace-nowrap" data-start-keys><Kbd keys="Ctrl" /><Kbd keys="V" /></span>{/if}
							{/if}
						</div>
					</div>
					<div class="srcs transition-opacity duration-100 {dragging ? 'opacity-[0.45]' : ''}" data-start-sources>
						{#each entries as entry (entry.id)}
							<button
								type="button"
								class="src flex min-h-[72px] min-w-0 items-center gap-3 rounded border border-line-strong bg-surface-3 px-3.5 py-3 text-left outline-none transition-colors duration-100 hover:bg-line-hover focus-visible:ring-2 focus-visible:ring-signal"
								aria-label={entry.label}
								data-start-source={entry.id}
								onclick={() => picker?.open(entry.id)}
							>
								<span class="src-icon flex h-10 w-10 flex-none items-center justify-center rounded border border-line bg-surface-2 text-fg-muted">
									<Icon name={entry.icon} className="h-4 w-4" />
								</span>
								<span class="src-text flex min-w-0 flex-1 flex-col">
									<span class="text-base font-semibold leading-[18px]">{entry.label}</span>
									{#if entry.hint}<span class="src-hint text-sm text-fg-muted">{entry.hint}</span>{/if}
								</span>
								<Icon name="chevron-right" className="src-chev h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
							</button>
						{/each}
					</div>
					{#if busy}
						<span class="mt-3 flex justify-center"><Spinner /></span>
					{/if}
					{#if error}
						<p class="mt-3 text-sm text-danger" role="alert">{error}</p>
					{/if}
				</Card>
			</section>

			<section
				class="flex min-w-0 flex-1 flex-col transition-opacity duration-100 {dragging ? 'opacity-[0.45]' : ''}"
				data-recent-edits
				data-state={firstUse ? 'first-use' : $recent.status}
			>
				<div class="rhead mb-3 flex min-h-9 flex-wrap items-center gap-x-4 gap-y-2 max-md:gap-2.5">
					{#if countLabel === undefined}
						<PageTitle title="Recent edits" />
					{:else}
						<PageTitle title="Recent edits" count={countLabel} countLabel="edits" countLabelOne="edit" />
					{/if}
					{#if !firstUse}
						<p class="rnote flex items-start gap-2 text-sm text-fg-subtle" data-edits-note>
							<Icon name="info" className="mt-px h-3.5 w-3.5 flex-shrink-0" />
							<span>Your changes are saved as you go. Edits stay here until you delete them.</span>
						</p>
						<div class="relative order-2 ml-auto w-[264px] max-w-full max-md:ml-0 max-md:w-full max-md:flex-[1_1_100%]" data-edits-search>
							<span class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle">
								<Icon name="search" className="h-3.5 w-3.5" />
							</span>
							<Input
								type="text"
								value={$recent.search}
								placeholder="Search by name"
								aria-label="Search edits by name"
								autocomplete="off"
								disabled={listOff}
								class="!pl-[34px] !pr-[34px]"
								oninput={(event: Event) => recent.setSearch((event.currentTarget as HTMLInputElement).value)}
							/>
							{#if $recent.search}
								<IconButton
									icon="close"
									label="Clear search"
									size="xs"
									class="absolute right-1 top-1/2 min-h-7 min-w-7 -translate-y-1/2"
									onclick={() => recent.clearSearch()}
								/>
							{/if}
						</div>
					{/if}
				</div>

				{#if $recent.status === 'error'}
					<LoadErrorState title="Couldn't load your edits" message="Check your connection and try again. Your edits are safe." onRetry={() => void recent.retry()} />
				{:else if $recent.status === 'loading'}
					<div class="sr-only" role="status">Loading your edits</div>
					<div class="edit-grid" aria-busy="true" data-edits-loading>
						{#each Array.from({ length: 12 }, (_, index) => index) as index (index)}
							<div class="animate-pulse motion-reduce:animate-none" aria-hidden="true">
								<div class="overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised">
									<div class="aspect-[4/3] bg-surface-2"></div>
									<div class="flex flex-col gap-2 border-t border-line px-3 py-3.5">
										<div class="h-3 w-[62%] rounded-sm bg-surface-2"></div>
										<div class="h-3 w-[40%] rounded-sm bg-surface-2"></div>
									</div>
								</div>
							</div>
						{/each}
					</div>
				{:else if firstUse}
					<EmptyState
						page={!emptyCompact}
						compact={emptyCompact}
						icon="sliders"
						title="No edits yet"
						description="Pick a picture to start. Your changes are saved as you go, and every edit shows up here."
					/>
					{#if history.length}
						<section class="mt-2" aria-label="Recent in History">
							<p class="mb-3 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Recent in History</p>
							<div class="edit-grid history" role="list" data-recent-history>
								{#each history as pick (pick.ref.path)}
									<EditTile
										id={pick.ref.path}
										name={fileStem(pick.ref.name ?? '') || 'Picture'}
										look={sourceLook('history')}
										time={relativeTime(pick.at)}
										picture={pick.ref.url}
										actionLabel="Start an edit"
										nameLink={false}
										ariaLabel="Start an edit from {fileStem(pick.ref.name ?? '')}"
										onopen={() => onpick(pick.ref)}
									/>
								{/each}
							</div>
						</section>
					{/if}
				{:else if $recent.edits.length === 0}
					<EmptyState
						compact
						icon="search"
						title="No matching edits"
						description="Nothing is named or based on “{$recent.query}”. Check the spelling or clear the search."
					>
						{#snippet actions()}
							<Button size="sm" variant="secondary" onclick={() => recent.clearSearch()}>Clear search</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<div
						class="edit-grid transition-opacity duration-100 {$recent.refreshing ? 'opacity-60' : ''}"
						role="list"
						aria-label="Recent edits"
						aria-busy={$recent.refreshing}
						data-edits-list
					>
						{#each $recent.edits as edit (edit.id)}
							{@const label = editLabel(edit)}
							{@const elsewhere = openMap.has(edit.id)}
							<EditTile
								id={edit.id}
								name={label.text}
								unnamed={label.unnamed}
								look={sourceLook(edit.source.kind)}
								time={relativeTime(edit.updated_at ?? edit.created_at)}
								picture={urls[pictureKey(edit)]}
								openElsewhere={elsewhere}
								actionLabel={elsewhere ? 'Go to open tab' : 'Continue this edit'}
								ariaLabel="{elsewhere ? 'Go to the open tab' : 'Continue this edit'}: {label.text}"
								editable
								renameValue={edit.name ?? ''}
								renamePlaceholder={label.unnamed ? label.text : ''}
								onopen={() => void open(edit)}
								onrename={(draft) => rename(edit, draft)}
								onduplicate={() => void duplicate(edit)}
								ondelete={() => void remove(edit)}
							/>
						{/each}
					</div>
					<Pagination class="mt-5 justify-center" size="sm" currentPage={$recent.page} {totalPages} onPageChange={(page) => recent.setPage(page)} />
				{/if}
			</section>
		</div>
	</div>

	{#if dragging}
		<div class="pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-signal/5 ring-1 ring-inset ring-signal max-md:hidden" aria-hidden="true" data-drop-overlay>
			<div class="flex flex-col items-center gap-1 rounded-xl border border-signal/60 bg-surface-1 px-10 py-6 text-center shadow-overlay">
				<span class="mb-2 flex h-12 w-12 items-center justify-center rounded-lg border border-signal/40 bg-signal/10 text-signal">
					<Icon name="upload" className="h-5 w-5" />
				</span>
				<span class="text-md font-semibold text-fg">Drop a picture to start an edit</span>
				<span class="text-sm text-fg-muted">Release it anywhere on this screen.</span>
			</div>
		</div>
	{/if}
</div>

<ImageSourcePicker bind:this={picker} {onpick} {onfile} />

<style>
	.start {
		container-type: inline-size;
		container-name: start;
	}

	.band {
		container-type: inline-size;
		container-name: band;
	}

	.srcs {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 12px;
	}

	.rnote {
		order: 3;
		flex: 1 1 100%;
	}

	.edit-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}

	.edit-grid.history > :global(*:nth-child(n + 5)) {
		display: none;
	}

	@container start (min-width: 960px) {
		.rnote {
			order: 1;
			flex: 0 1 auto;
		}
	}

	@container start (min-width: 560px) {
		.edit-grid {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
		.edit-grid.history > :global(*:nth-child(n + 4)) {
			display: none;
		}
	}

	@container start (min-width: 820px) {
		.edit-grid {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
		.edit-grid.history > :global(*:nth-child(n + 5)) {
			display: none;
		}
	}

	@container start (min-width: 1240px) {
		.edit-grid {
			grid-template-columns: repeat(6, minmax(0, 1fr));
		}
		.edit-grid.history > :global(*:nth-child(n + 5)) {
			display: block;
		}
		.edit-grid.history > :global(*:nth-child(n + 7)) {
			display: none;
		}
	}

	@container band (max-width: 880px) {
		.srcs {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@container band (max-width: 520px) {
		.band-sub,
		.band-hint {
			display: none;
		}
		.srcs {
			gap: 8px;
		}
		.src {
			flex-direction: column;
			justify-content: center;
			gap: 4px;
			min-height: 68px;
			padding: 8px;
			text-align: center;
		}
		.src-icon {
			width: auto;
			height: auto;
			border: 0;
			background: none;
		}
		.src-text {
			flex: none;
			align-items: center;
		}
		.src-hint,
		.src :global(.src-chev) {
			display: none;
		}
	}

	@media (max-width: 767px) {
		.edit-grid {
			gap: 10px;
		}
	}
</style>
