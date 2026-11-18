<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import Icon from '$lib/components/Icon.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, EmptyState, IconButton, Input, LoadErrorState, Pagination } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { EditSummary } from '$lib/services/api/edits';
	import { confirmDialog } from '$lib/stores/confirm';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import {
		createEditPictures,
		createRecentEdits,
		editLabel,
		openEditTabs,
		pageCount,
		pictureKey,
		relativeTime,
		sourceLook,
		type ContinueResult
	} from '$lib/tool-tabs/image/recentEdits';
	import EditTile from './EditTile.svelte';
	import JustifiedTiles from './JustifiedTiles.svelte';

	let {
		tabId,
		inPlace = true,
		onclose
	}: {
		tabId: string;
		inPlace?: boolean;
		onclose: () => void;
	} = $props();

	const recent = createRecentEdits({
		edits: api,
		library: api,
		tabId: untrack(() => tabId),
		openTabs: () => get(tabsStore).tabs,
		focusTab: (id) => tabsStore.setActiveTab(id),
		restoreTab: (patch) => {
			if (untrack(() => inPlace)) {
				tabsStore.restoreToolTab(untrack(() => tabId), patch);
				return;
			}
			const id = tabsStore.openToolTab('image', patch.source, { activeTool: patch.activeTool, state: patch.state, fresh: true });
			tabsStore.setActiveTab(id);
		}
	});
	const pictures = createEditPictures(api);

	const SKELETON_ASPECTS = [1.5, 0.75, 1, 1.78, 1, 0.7, 1.33, 1, 0.75, 1.78, 1, 1.5];
	const SKELETON_ITEMS = SKELETON_ASPECTS.map((aspect, index) => ({ key: `skeleton-${index}`, aspect }));

	let urls = $state<Record<string, string | null>>({});
	const requested = new Set<string>();

	let openMap = $derived(openEditTabs($tabsStore.tabs, tabId));
	let listOff = $derived($recent.status !== 'ready');
	let totalPages = $derived(pageCount($recent.total));
	let editItems = $derived($recent.edits.map((edit) => ({ key: edit.id, edit, aspect: null as number | null })));
	let subtitle = $derived(
		$recent.status !== 'ready' ? '' : $recent.query ? `${$recent.total} of ${$recent.overall} edits` : `${$recent.overall} ${$recent.overall === 1 ? 'edit' : 'edits'}`
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

	function explain(result: ContinueResult) {
		if (result.kind === 'missing') toasts.error('That edit no longer exists.');
		else if (result.kind === 'source-missing') toasts.error('The picture for this edit is no longer available.');
		else if (result.kind === 'failed') toasts.error('Could not open this edit. Try again.');
	}

	async function open(edit: EditSummary) {
		const result = await recent.continueEdit(edit.id);
		explain(result);
		if (result.kind === 'opened' || result.kind === 'focused') onclose();
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

<BaseModal isOpen={true} title="Recent edits" {subtitle} sizeClass="md:w-[min(1100px,92vw)] md:h-[85vh]" on:close={onclose}>
	<div class="flex min-h-full flex-col gap-4 p-5 max-md:p-3" data-recent-edits data-state={$recent.status}>
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			<p class="flex min-w-0 flex-1 items-start gap-2 text-sm text-fg-subtle" data-edits-note>
				<Icon name="info" className="mt-px h-3.5 w-3.5 flex-shrink-0" />
				<span>Your changes are saved as you go. Edits stay here until you delete them.</span>
			</p>
			<div class="relative w-[264px] max-w-full max-md:w-full" data-edits-search>
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
		</div>

		{#if $recent.status === 'error'}
			<LoadErrorState title="Couldn't load your edits" message="Check your connection and try again. Your edits are safe." onRetry={() => void recent.retry()} />
		{:else if $recent.status === 'loading'}
			<div class="sr-only" role="status">Loading your edits</div>
			<JustifiedTiles items={SKELETON_ITEMS} aria-busy="true" data-edits-loading>
				{#snippet tile(_item, box)}
					<div class="animate-pulse motion-reduce:animate-none" aria-hidden="true">
						<div class="overflow-hidden rounded-lg border border-line bg-surface-1 shadow-raised">
							<div class="bg-surface-2" style="height: {box.height}px"></div>
							<div class="flex flex-col gap-2 border-t border-line px-3 py-3.5">
								<div class="h-3 w-[62%] rounded-sm bg-surface-2"></div>
								<div class="h-3 w-[40%] rounded-sm bg-surface-2"></div>
							</div>
						</div>
					</div>
				{/snippet}
			</JustifiedTiles>
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
			<JustifiedTiles
				items={editItems}
				class="transition-opacity duration-100 {$recent.refreshing ? 'opacity-60' : ''}"
				role="list"
				aria-label="Recent edits"
				aria-busy={$recent.refreshing}
				data-edits-list
			>
				{#snippet tile({ edit }, box, report)}
					{@const label = editLabel(edit)}
					{@const elsewhere = openMap.has(edit.id)}
					<EditTile
						id={edit.id}
						name={label.text}
						unnamed={label.unnamed}
						look={sourceLook(edit.source.kind)}
						time={relativeTime(edit.updated_at ?? edit.created_at)}
						picture={urls[pictureKey(edit)]}
						tile={box}
						onsize={report}
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
				{/snippet}
			</JustifiedTiles>
			<Pagination class="mt-2 justify-center" size="sm" currentPage={$recent.page} {totalPages} onPageChange={(page) => recent.setPage(page)} />
		{/if}
	</div>
</BaseModal>
