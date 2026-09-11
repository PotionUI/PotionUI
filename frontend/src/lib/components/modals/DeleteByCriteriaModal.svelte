<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import BaseModal from './BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Alert, Kbd, Switch } from '$lib/components/ui';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from './confirmKeyboard';
	import {
		buildDeleteByCriteriaRequest,
		hasCriteria,
		initialDeleteByCriteriaState,
		type AgeMode,
		type BulkDeleteByCriteriaRequestBody,
		type DeleteByCriteriaCriteriaConfig
	} from './deleteByCriteria';
	import type { Tag } from '$lib/types/history';

	export let onClose: () => void;
	/** Which criteria to render and send - see `DeleteByCriteriaCriteriaConfig`. */
	export let criteria: DeleteByCriteriaCriteriaConfig;
	export let title: string;
	/** Singular noun for the affected rows, e.g. "generation" or "item" - pluralized with a trailing 's'. */
	export let itemLabel: string;
	export let availableTags: Tag[] = [];
	export let mediaTypeOptions: Array<{ value: string; label: string }> = [];
	export let count: (request: BulkDeleteByCriteriaRequestBody) => Promise<number>;
	export let remove: (
		request: BulkDeleteByCriteriaRequestBody
	) => Promise<{ deleted_count: number; files_deleted: number }>;

	const COUNT_DEBOUNCE_MS = 300;

	// Tags fetched without a color (e.g. never had one assigned) would
	// otherwise render `style="background-color: "` on the dot below — an
	// empty declaration the browser drops entirely, leaving a fully
	// transparent 8px dot that still reserves its box: a "weird empty space"
	// to the left of the tag name with no visible cause.
	const FALLBACK_TAG_COLOR = 'rgb(var(--fg-subtle))';

	$: itemLabelPlural = `${itemLabel}s`;

	let state = initialDeleteByCriteriaState();
	let matchCount = 0;
	let counting = false;
	let deleting = false;
	let countDebounceHandle: ReturnType<typeof setTimeout> | undefined;

	const settlementGate = createConfirmSettlementGate();
	$: criteriaSelected = hasCriteria(state, criteria);
	$: canConfirm = criteriaSelected && matchCount > 0 && !deleting && !counting;

	function scheduleCount() {
		clearTimeout(countDebounceHandle);
		if (!hasCriteria(state, criteria)) {
			matchCount = 0;
			counting = false;
			return;
		}
		counting = true;
		countDebounceHandle = setTimeout(updateMatchCount, COUNT_DEBOUNCE_MS);
	}

	async function updateMatchCount() {
		if (!hasCriteria(state, criteria)) {
			matchCount = 0;
			counting = false;
			return;
		}
		try {
			matchCount = await count(buildDeleteByCriteriaRequest(state, criteria));
		} catch (e) {
			logger.error('Failed to count:', e);
		} finally {
			counting = false;
		}
	}

	async function handleDelete() {
		if (!hasCriteria(state, criteria)) return;
		deleting = true;
		try {
			await remove(buildDeleteByCriteriaRequest(state, criteria));
			state = initialDeleteByCriteriaState();
			matchCount = 0;
			onClose();
		} catch (e) {
			logger.error('Failed to delete by criteria:', e);
			settlementGate.reset();
		} finally {
			deleting = false;
		}
	}

	function toggleTag(tagId: string) {
		state.tagIds = state.tagIds.includes(tagId)
			? state.tagIds.filter((id) => id !== tagId)
			: [...state.tagIds, tagId];
		scheduleCount();
	}

	function setAgeMode(mode: AgeMode) {
		state.ageMode = state.ageMode === mode ? 'none' : mode;
		scheduleCount();
	}

	function setMediaType(value: string) {
		state.mediaType = state.mediaType === value ? null : value;
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
	{title}
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
			{itemLabelPlural[0].toUpperCase()}{itemLabelPlural.slice(1)} matching every criterion below will be permanently deleted.
		</p>

		{#if criteria.tags && availableTags.length > 0}
			<div>
				<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Tags (all selected)</p>
				<div class="flex flex-wrap gap-2 max-h-32 overflow-y-auto p-1">
					{#each availableTags as tag}
						<button
							class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition-all"
							style={tagPillStyle(tag.color, state.tagIds.includes(tag.id))}
							on:click={() => toggleTag(tag.id)}
							disabled={deleting}
						>
							<span
								class="w-2 h-2 rounded-full flex-shrink-0"
								style="background-color: {tag.color || FALLBACK_TAG_COLOR}"
							></span>
							{tag.name}
							{#if state.tagIds.includes(tag.id)}
								<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>
								</svg>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		{#if criteria.mediaType && mediaTypeOptions.length > 0}
			<div>
				<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Media type</p>
				<div class="inline-flex items-center gap-1">
					{#each mediaTypeOptions as option}
						<button
							type="button"
							class="rounded px-2.5 py-1 text-xs font-medium transition-colors {state.mediaType === option.value
								? 'bg-signal/10 text-signal'
								: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
							disabled={deleting}
							on:click={() => setMediaType(option.value)}
						>
							{option.label}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		{#if criteria.age}
			<div>
				<p class="text-xs text-fg-muted mb-2 uppercase tracking-wide font-medium">Age</p>
				<div class="inline-flex items-center gap-1 mb-2">
					<button
						type="button"
						class="rounded px-2.5 py-1 text-xs font-medium transition-colors {state.ageMode === 'older_than_days'
							? 'bg-signal/10 text-signal'
							: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
						disabled={deleting}
						on:click={() => setAgeMode('older_than_days')}
					>
						Older than N days
					</button>
					<button
						type="button"
						class="rounded px-2.5 py-1 text-xs font-medium transition-colors {state.ageMode === 'date_range'
							? 'bg-signal/10 text-signal'
							: 'bg-surface-2 text-fg-muted hover:bg-surface-3'}"
						disabled={deleting}
						on:click={() => setAgeMode('date_range')}
					>
						Date range
					</button>
				</div>

				{#if state.ageMode === 'older_than_days'}
					<input
						type="number"
						min="1"
						class="input w-32 text-sm"
						placeholder="Days"
						disabled={deleting}
						bind:value={state.olderThanDays}
						on:input={scheduleCount}
					/>
				{:else if state.ageMode === 'date_range'}
					<div class="flex items-center gap-2">
						<input
							type="date"
							class="input w-full text-sm"
							aria-label="Created from"
							disabled={deleting}
							bind:value={state.createdFrom}
							on:input={scheduleCount}
						/>
						<span class="text-xs text-fg-subtle">to</span>
						<input
							type="date"
							class="input w-full text-sm"
							aria-label="Created to"
							disabled={deleting}
							bind:value={state.createdTo}
							on:input={scheduleCount}
						/>
					</div>
				{/if}
			</div>
		{/if}

		{#if criteria.status || criteria.withoutMedia || criteria.keepFavorites}
			<div class="space-y-1">
				{#if criteria.status}
					<div class="flex items-center justify-between gap-3 py-1">
						<span class="text-sm text-fg">Only failed / cancelled</span>
						<Switch
							label="Only include failed or cancelled generations"
							checked={state.failedOrCancelledOnly}
							disabled={deleting}
							onchange={(checked) => {
								state.failedOrCancelledOnly = checked;
								scheduleCount();
							}}
						/>
					</div>
				{/if}
				{#if criteria.withoutMedia}
					<div class="flex items-center justify-between gap-3 py-1">
						<span class="text-sm text-fg">Without media files</span>
						<Switch
							label="Only include generations without media files"
							checked={state.withoutMedia}
							disabled={deleting}
							onchange={(checked) => {
								state.withoutMedia = checked;
								scheduleCount();
							}}
						/>
					</div>
				{/if}
				{#if criteria.keepFavorites}
					<div class="flex items-center justify-between gap-3 py-1">
						<span class="text-sm text-fg">Keep favorites</span>
						<Switch
							label="Never delete favorited generations"
							checked={state.keepFavorites}
							disabled={deleting}
							onchange={(checked) => {
								state.keepFavorites = checked;
								scheduleCount();
							}}
						/>
					</div>
				{/if}
			</div>
		{/if}

		<div class="p-3 rounded-lg bg-surface-2/60 border border-line-strong/50 min-h-[52px] flex items-center">
			{#if !criteriaSelected}
				<p class="text-sm text-fg-subtle">Choose at least one criterion to see how many {itemLabelPlural} will be affected.</p>
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
						<span class="text-fg-muted"> {matchCount !== 1 ? itemLabelPlural : itemLabel} will be deleted.</span>
					</p>
				</div>
			{/if}
		</div>

		{#if matchCount > 0}
			<Alert variant="warning" live="polite">
				This will permanently delete {matchCount} {matchCount !== 1 ? itemLabelPlural : itemLabel} and all their files from your disk. This action cannot be undone.
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
