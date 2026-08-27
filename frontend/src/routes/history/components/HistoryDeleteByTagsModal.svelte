<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { historyStore } from '$lib/stores/history';
	import { api } from '$lib/services/api/index';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Alert, Kbd } from '$lib/components/ui';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';

	export let onClose: () => void;

	// Tags fetched without a color (e.g. never had one assigned) would
	// otherwise render `style="background-color: "` on the dot below — an
	// empty declaration the browser drops entirely, leaving a fully
	// transparent 8px dot that still reserves its box: a "weird empty space"
	// to the left of the tag name with no visible cause.
	const FALLBACK_TAG_COLOR = 'rgb(var(--fg-subtle))';

	$: currentState = $historyStore;
	$: availableTags = currentState.availableTags;

	let selectedDeleteTagIds: string[] = [];
	let deleteByTagsCount = 0;
	let countingByTags = false;
	let deletingByTags = false;

	const settlementGate = createConfirmSettlementGate();
	$: canConfirm =
		selectedDeleteTagIds.length > 0 && deleteByTagsCount > 0 && !deletingByTags && !countingByTags;

	async function updateDeleteByTagsCount() {
		if (selectedDeleteTagIds.length === 0) {
			deleteByTagsCount = 0;
			return;
		}
		countingByTags = true;
		try {
			const response = await api.countGenerationsByTags(selectedDeleteTagIds);
			if (response.success && response.data) {
				deleteByTagsCount = response.data.count;
			}
		} catch (e) {
			logger.error('Failed to count:', e);
		} finally {
			countingByTags = false;
		}
	}

	async function handleDeleteByTags() {
		if (selectedDeleteTagIds.length === 0) return;
		deletingByTags = true;
		try {
			const response = await historyStore.bulkDeleteByTags(selectedDeleteTagIds);
			if (response.success) {
				selectedDeleteTagIds = [];
				deleteByTagsCount = 0;
				onClose();
			}
		} catch (e) {
			logger.error('Failed to delete by tags:', e);
			settlementGate.reset();
		} finally {
			deletingByTags = false;
		}
	}

	function toggleDeleteTag(tagId: string) {
		if (selectedDeleteTagIds.includes(tagId)) {
			selectedDeleteTagIds = selectedDeleteTagIds.filter((id) => id !== tagId);
		} else {
			selectedDeleteTagIds = [...selectedDeleteTagIds, tagId];
		}
		updateDeleteByTagsCount();
	}

	// A tag without a color renders the pill in neutral tones instead of
	// splicing an alpha suffix onto `undefined` (invalid CSS the browser
	// would silently drop).
	function tagPillStyle(color: string | undefined, selected: boolean): string {
		if (!selected) return 'color: rgb(var(--fg-subtle)); border-color: rgb(var(--line-strong));';
		if (!color) return 'background-color: rgb(var(--surface-3)); color: rgb(var(--fg)); border-color: rgb(var(--line-hover));';
		return `background-color: ${color}20; color: ${color}; border-color: ${color}60;`;
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, canConfirm, handleDeleteByTags);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (deletingByTags) return;
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	isOpen={true}
	title="Delete Generations by Tags"
	sizeClass="md:max-w-lg md:w-full"
	closeable={!deletingByTags}
	dialogRole="alertdialog"
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="warning" className="w-5 h-5 text-danger" />
	</svelte:fragment>
	<div class="p-6">
		<p class="text-sm text-fg-muted mb-5">
			Select tags. All generations matching ALL selected tags will be permanently deleted.
		</p>

		{#if availableTags.length === 0}
			<p class="text-sm text-fg-subtle mb-5">No tags available.</p>
		{:else}
			<div class="mb-5">
				<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Select Tags</p>
				<div class="flex flex-wrap gap-2 max-h-48 overflow-y-auto p-1">
					{#each availableTags as tag}
						<button
							class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all"
							style={tagPillStyle(tag.color, selectedDeleteTagIds.includes(tag.id))}
							on:click={() => toggleDeleteTag(tag.id)}
							disabled={deletingByTags}
						>
							<span
								class="w-2 h-2 rounded-full flex-shrink-0"
								style="background-color: {tag.color || FALLBACK_TAG_COLOR}"
							></span>
							{tag.name}
							{#if selectedDeleteTagIds.includes(tag.id)}
								<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>
								</svg>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<div class="mb-5 p-3 rounded-lg bg-surface-2/60 border border-line-strong/50 min-h-[52px] flex items-center">
			{#if selectedDeleteTagIds.length === 0}
				<p class="text-sm text-fg-subtle">Select at least one tag to see how many generations will be affected.</p>
			{:else if countingByTags}
				<div class="flex items-center gap-2 text-sm text-fg-muted">
					<svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
					</svg>
					Counting...
				</div>
			{:else}
				<div class="flex items-center gap-2">
					<svg class="w-4 h-4 text-danger flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
					</svg>
					<p class="text-sm">
						<span class="font-semibold text-danger font-mono tabular-nums">{deleteByTagsCount}</span>
						<span class="text-fg-muted"> generation{deleteByTagsCount !== 1 ? 's' : ''} will be deleted.</span>
					</p>
				</div>
			{/if}
		</div>

		{#if deleteByTagsCount > 0}
			<Alert variant="warning" live="polite">
				This will permanently delete {deleteByTagsCount} generation{deleteByTagsCount !== 1 ? 's' : ''} and all their files from your disk. This action cannot be undone.
			</Alert>
		{/if}
	</div>
	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-3 px-6 py-4">
			<Button variant="secondary" disabled={deletingByTags} onclick={handleCancel}>
				<span class="inline-flex items-center gap-2">
					Cancel
					<Kbd keys="Esc" />
				</span>
			</Button>
			<Button variant="danger" disabled={!canConfirm} loading={deletingByTags} onclick={handleConfirm}>
				<span class="inline-flex items-center gap-2">
					Confirm
					<Kbd keys="Enter" />
				</span>
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
