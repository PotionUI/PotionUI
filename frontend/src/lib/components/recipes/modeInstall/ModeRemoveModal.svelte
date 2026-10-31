<script lang="ts">
	import { onMount } from 'svelte';
	import { Alert, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { api } from '$lib/services/api/index';
	import type { ModeRemovalPreview } from '$lib/services/api/recipeModes';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { formatBytesShort } from '$lib/utils/format';
	import { groupKept, removalDeleteIds, removeConfirmLabel } from './modeRemoveModel';

	let {
		recipeId,
		modeId,
		modeLabel,
		onClose,
		onRemoved
	}: {
		recipeId: string;
		modeId: string;
		modeLabel: string;
		onClose: () => void;
		onRemoved: (freedBytes: number) => void;
	} = $props();

	let preview = $state<ModeRemovalPreview | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let busy = $state(false);
	let error = $state('');

	const kept = $derived(preview ? groupKept(preview.keep) : []);
	const deleteCount = $derived(preview?.delete.length ?? 0);
	const frees = $derived(preview ? formatBytesShort(preview.frees_bytes) : '');
	const targetWord = $derived(preview && preview.target !== 'native' ? 'on this worker' : 'on this computer');

	onMount(async () => {
		try {
			preview = await api.getModeRemovalPreview(recipeId, modeId);
		} catch (err) {
			loadError = getApiErrorMessage(err, "Couldn't check which files this mode uses.");
		} finally {
			loading = false;
		}
	});

	async function confirm() {
		if (!preview || busy || deleteCount === 0) return false;
		busy = true;
		error = '';
		try {
			const result = await api.removeModeFiles(recipeId, modeId, removalDeleteIds(preview));
			onRemoved(result.freed_bytes);
			return true;
		} catch (err) {
			error = getApiErrorMessage(err, "Couldn't remove these files.");
			return false;
		} finally {
			busy = false;
		}
	}
</script>

{#snippet frees_note()}
	{#if error}
		<span class="flex items-center gap-1.5 text-sm text-danger">
			<Icon name="warning" className="h-3.5 w-3.5 shrink-0" />{error}
		</span>
	{:else if preview}
		<span class="font-mono text-sm tabular-nums text-fg-muted" data-remove-frees>Frees {frees}</span>
	{/if}
{/snippet}

<ConfirmDialog
	isOpen={true}
	title="Remove {modeLabel} files?"
	subtitle={preview ? `Frees ${frees} ${targetWord}.` : ''}
		size="md"
	compact
		stackOnPhone
	splitOnPhone
	subtitleWrap
	{busy}
	confirmLabel={removeConfirmLabel(preview)}
	confirmVariant="danger"
	confirmDisabled={loading || !!loadError || deleteCount === 0}
	cancelLabel="Keep files"
	leftActions={frees_note}
	onConfirm={confirm}
	onCancel={onClose}
>
	<div data-mode-remove>
		{#if loading}
			<div class="flex justify-center py-10"><Spinner size="md" /></div>
		{:else if loadError}
			<div class="p-5"><Alert variant="danger" density="compact">{loadError}</Alert></div>
		{:else if preview}
			<section class="border-b border-line px-5 py-4" data-remove-deleted>
				<h3 class="mb-2 font-mono text-sm uppercase tracking-[0.07em] text-danger">
					Deleted from disk {deleteCount} {deleteCount === 1 ? 'file' : 'files'}
				</h3>
				{#if deleteCount === 0}
					<p class="text-sm text-fg-muted">This mode has no files of its own to delete.</p>
				{:else}
					<ul class="divide-y divide-line rounded border border-line">
						{#each preview.delete as file (file.model_id)}
							<li class="flex items-center gap-3 px-3 py-2">
								<Icon name="trash" className="h-3.5 w-3.5 shrink-0 text-danger" />
								<div class="min-w-0 flex-1">
									<div class="truncate text-base text-fg">{file.label}</div>
									<div class="truncate text-sm text-fg-muted">{file.filename}</div>
								</div>
								{#if file.size_bytes != null}
									<span class="shrink-0 font-mono text-sm tabular-nums text-fg-muted">{formatBytesShort(file.size_bytes)}</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="px-5 py-4" data-remove-kept>
				<h3 class="mb-2 font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
					Kept {kept.length} {kept.length === 1 ? 'item' : 'items'}
				</h3>
				{#if kept.length === 0}
					<p class="text-sm text-fg-muted">Nothing else is kept. No other mode uses these files.</p>
				{:else}
					<ul class="divide-y divide-line rounded border border-line">
						{#each kept as group (group.key)}
							<li class="flex items-center gap-3 px-3 py-2">
								<Icon name="lock" className="h-3.5 w-3.5 shrink-0 text-fg-subtle" />
								<div class="min-w-0 flex-1">
									<div class="truncate text-base text-fg">{group.title}</div>
									<div class="text-sm text-fg-muted">{group.reason}</div>
								</div>
								{#if group.bytes > 0}
									<span class="shrink-0 font-mono text-sm tabular-nums text-fg-muted">{formatBytesShort(group.bytes)}</span>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
				<p class="mt-3 text-sm text-fg-muted">
					{modeLabel} will show as not set up until you install it again{preview.clears_defaults.length > 0
						? ', and its default file choice is cleared'
						: ''}. Your own files are never deleted.
				</p>
			</section>
		{/if}
	</div>
</ConfirmDialog>
