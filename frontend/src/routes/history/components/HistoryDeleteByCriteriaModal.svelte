<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { historyStore } from '$lib/stores/history';
	import { api } from '$lib/services/api/index';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Alert, Kbd, Switch } from '$lib/components/ui';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from '$lib/components/modals/confirmKeyboard';
	import {
		buildDeleteByCriteriaRequest,
		hasCriteria,
		initialDeleteByCriteriaState,
		type AgeMode
	} from './deleteByCriteria';

	export let onClose: () => void;

	const COUNT_DEBOUNCE_MS = 300;

	// Tags fetched without a color (e.g. never had one assigned) would
	// otherwise render `style="background-color: "` on the dot below — an
	// empty declaration the browser drops entirely, leaving a fully
	// transparent 8px dot that still reserves its box: a "weird empty space"
	// to the left of the tag name with no visible cause.
	const FALLBACK_TAG_COLOR = 'rgb(var(--fg-subtle))';

	$: currentState = $historyStore;
	$: availableTags = currentState.availableTags;

	let criteria = initialDeleteByCriteriaState();
	let matchCount = 0;
	let counting = false;
	let deleting = false;
	let countDebounceHandle: ReturnType<typeof setTimeout> | undefined;

	const settlementGate = createConfirmSettlementGate();
	$: criteriaSelected = hasCriteria(criteria);
	$: canConfirm = criteriaSelected && matchCount > 0 && !deleting && !counting;

	function scheduleCount() {
		clearTimeout(countDebounceHandle);
		if (!hasCriteria(criteria)) {
			matchCount = 0;
			counting = false;
			return;
		}
		counting = true;
		countDebounceHandle = setTimeout(updateMatchCount, COUNT_DEBOUNCE_MS);
	}

	async function updateMatchCount() {
		if (!hasCriteria(criteria)) {
			matchCount = 0;
			counting = false;
			return;
		}
		try {
			const response = await api.countGenerationsByCriteria(buildDeleteByCriteriaRequest(criteria));
			if (response.success && response.data) {
				matchCount = response.data.count;
			}
		} catch (e) {
			logger.error('Failed to count:', e);
		} finally {
			counting = false;
		}
	}

	async function handleDelete() {
		if (!hasCriteria(criteria)) return;
		deleting = true;
		try {
			const response = await historyStore.bulkDeleteByCriteria(buildDeleteByCriteriaRequest(criteria));
			if (response.success) {
				criteria = initialDeleteByCriteriaState();
				matchCount = 0;
				onClose();
			}
		} catch (e) {
			logger.error('Failed to delete by criteria:', e);
			settlementGate.reset();
		} finally {
			deleting = false;
		}
	}

	function toggleTag(tagId: string) {
		criteria.tagIds = criteria.tagIds.includes(tagId)
			? criteria.tagIds.filter((id) => id !== tagId)
			: [...criteria.tagIds, tagId];
		scheduleCount();
	}

	function setAgeMode(mode: AgeMode) {
		criteria.ageMode = criteria.ageMode === mode ? 'none' : mode;
		scheduleCount();
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
		settleIfEligible(settlementGate, canConfirm, handleDelete);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (deleting) return;
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	isOpen={true}
	title="Delete Generations by Criteria"
	sizeClass="md:max-w-lg md:w-full"
	closeable={!deleting}
	dialogRole="alertdialog"
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="warning" className="w-5 h-5 text-danger" />
	</svelte:fragment>
	<div class="p-6 space-y-5">
		<p class="text-sm text-fg-muted">
			Generations matching every criterion below will be permanently deleted.
		</p>

		{#if availableTags.length > 0}
			<div>
				<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Tags (all selected)</p>
				<div class="flex flex-wrap gap-2 max-h-32 overflow-y-auto p-1">
					{#each availableTags as tag}
						<button
							class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all"
							style={tagPillStyle(tag.color, criteria.tagIds.includes(tag.id))}
							on:click={() => toggleTag(tag.id)}
							disabled={deleting}
						>
							<span
								class="w-2 h-2 rounded-full flex-shrink-0"
								style="background-color: {tag.color || FALLBACK_TAG_COLOR}"
							></span>
							{tag.name}
							{#if criteria.tagIds.includes(tag.id)}
								<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>
								</svg>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<div>
			<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Age</p>
			<div class="inline-flex items-center gap-1 mb-2">
				<button
					type="button"
					class="rounded px-2.5 py-1 text-xs font-medium transition-colors {criteria.ageMode === 'older_than_days'
						? 'bg-signal/10 text-signal'
						: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
					disabled={deleting}
					on:click={() => setAgeMode('older_than_days')}
				>
					Older than N days
				</button>
				<button
					type="button"
					class="rounded px-2.5 py-1 text-xs font-medium transition-colors {criteria.ageMode === 'date_range'
						? 'bg-signal/10 text-signal'
						: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
					disabled={deleting}
					on:click={() => setAgeMode('date_range')}
				>
					Date range
				</button>
			</div>

			{#if criteria.ageMode === 'older_than_days'}
				<input
					type="number"
					min="1"
					class="input w-32 text-sm"
					placeholder="Days"
					disabled={deleting}
					bind:value={criteria.olderThanDays}
					on:input={scheduleCount}
				/>
			{:else if criteria.ageMode === 'date_range'}
				<div class="flex items-center gap-2">
					<input
						type="date"
						class="input w-full text-sm"
						aria-label="Created from"
						disabled={deleting}
						bind:value={criteria.createdFrom}
						on:input={scheduleCount}
					/>
					<span class="text-xs text-fg-subtle">to</span>
					<input
						type="date"
						class="input w-full text-sm"
						aria-label="Created to"
						disabled={deleting}
						bind:value={criteria.createdTo}
						on:input={scheduleCount}
					/>
				</div>
			{/if}
		</div>

		<div class="space-y-1">
			<div class="flex items-center justify-between gap-3 py-1">
				<span class="text-sm text-fg">Only failed / cancelled</span>
				<Switch
					label="Only include failed or cancelled generations"
					checked={criteria.failedOrCancelledOnly}
					disabled={deleting}
					onchange={(checked) => {
						criteria.failedOrCancelledOnly = checked;
						scheduleCount();
					}}
				/>
			</div>
			<div class="flex items-center justify-between gap-3 py-1">
				<span class="text-sm text-fg">Without media files</span>
				<Switch
					label="Only include generations without media files"
					checked={criteria.withoutMedia}
					disabled={deleting}
					onchange={(checked) => {
						criteria.withoutMedia = checked;
						scheduleCount();
					}}
				/>
			</div>
			<div class="flex items-center justify-between gap-3 py-1">
				<span class="text-sm text-fg">Keep favorites</span>
				<Switch
					label="Never delete favorited generations"
					checked={criteria.keepFavorites}
					disabled={deleting}
					onchange={(checked) => {
						criteria.keepFavorites = checked;
						scheduleCount();
					}}
				/>
			</div>
		</div>

		<div class="p-3 rounded-lg bg-surface-2/60 border border-line-strong/50 min-h-[52px] flex items-center">
			{#if !criteriaSelected}
				<p class="text-sm text-fg-subtle">Choose at least one criterion to see how many generations will be affected.</p>
			{:else if counting}
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
						<span class="font-semibold text-danger font-mono tabular-nums">{matchCount}</span>
						<span class="text-fg-muted"> generation{matchCount !== 1 ? 's' : ''} will be deleted.</span>
					</p>
				</div>
			{/if}
		</div>

		{#if matchCount > 0}
			<Alert variant="warning" live="polite">
				This will permanently delete {matchCount} generation{matchCount !== 1 ? 's' : ''} and all their files from your disk. This action cannot be undone.
			</Alert>
		{/if}
	</div>
	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-3 px-6 py-4">
			<Button variant="secondary" disabled={deleting} onclick={handleCancel}>
				<span class="inline-flex items-center gap-2">
					Cancel
					<Kbd keys="Esc" />
				</span>
			</Button>
			<Button variant="danger" disabled={!canConfirm} loading={deleting} onclick={handleConfirm}>
				<span class="inline-flex items-center gap-2">
					Confirm
					<Kbd keys="Enter" />
				</span>
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
