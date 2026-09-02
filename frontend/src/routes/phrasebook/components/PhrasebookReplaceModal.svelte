<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Badge, Button, Input, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import type {
		PhrasebookBatchOutcome,
		PhrasebookBatchPreview,
		PhrasebookFindValueHit,
		PhrasebookReplaceParams
	} from '$lib/types/api';
	import { apiErrorDetail, diffSegments, type FindFilters } from '../phrasebookSearch';

	let {
		isOpen,
		values,
		filters,
		onClose,
		onApplied
	}: {
		isOpen: boolean;
		values: PhrasebookFindValueHit[];
		filters: FindFilters;
		onClose: () => void;
		onApplied: (outcome: PhrasebookBatchOutcome) => void;
	} = $props();

	let replacement = $state('');
	let inLabel = $state(true);
	let inValue = $state(true);
	let preview = $state<PhrasebookBatchPreview | null>(null);
	let previewing = $state(false);
	let applying = $state(false);
	let error = $state<string | null>(null);
	let requestSeq = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;

	let labelsById = $derived(new Map(values.map((v) => [v.id, v.label])));
	let valueIds = $derived(values.map((v) => v.id));

	function params(): PhrasebookReplaceParams | null {
		const fields: PhrasebookReplaceParams['fields'] = [];
		if (inLabel) fields.push('label');
		if (inValue) fields.push('value');
		if (fields.length === 0 || values.length === 0) return null;
		return {
			find: filters.query.trim(),
			replace: replacement,
			mode: filters.mode,
			case_sensitive: filters.caseSensitive,
			fields
		};
	}

	async function loadPreview() {
		const seq = ++requestSeq;
		const body = params();
		if (!body) {
			preview = null;
			previewing = false;
			return;
		}
		previewing = true;
		error = null;
		try {
			const response = await api.previewPhrasebookBatch('replace', valueIds, { ...body });
			if (seq !== requestSeq) return;
			if (response.success && response.data) {
				preview = response.data;
			} else {
				preview = null;
				error = response.message || response.error || 'Preview failed';
			}
		} catch (e) {
			if (seq !== requestSeq) return;
			const detail = apiErrorDetail(e);
			preview = null;
			error = detail?.message ?? 'Preview failed';
			if (!detail) logger.error('Replace preview failed:', e);
		} finally {
			if (seq === requestSeq) previewing = false;
		}
	}

	function schedulePreview() {
		if (timer) clearTimeout(timer);
		timer = setTimeout(() => {
			timer = null;
			loadPreview();
		}, 250);
	}

	$effect(() => {
		if (!isOpen) return;
		untrack(() => {
			replacement = '';
			inLabel = filters.inLabel;
			inValue = filters.inValue;
			preview = null;
			error = null;
			loadPreview();
		});
		return () => {
			requestSeq++;
			if (timer) clearTimeout(timer);
			timer = null;
		};
	});

	async function apply() {
		const body = params();
		if (!body || applying) return;
		applying = true;
		error = null;
		try {
			const response = await api.runPhrasebookBatch('replace', valueIds, { ...body });
			if (response.success && response.data) {
				onApplied(response.data);
			} else {
				error = response.message || response.error || 'Replace failed';
			}
		} catch (e) {
			const detail = apiErrorDetail(e);
			error = detail?.message ?? 'Replace failed';
			if (!detail) logger.error('Replace failed:', e);
		} finally {
			applying = false;
		}
	}

	let changed = $derived(preview?.changed ?? 0);
	let unchanged = $derived(preview?.unchanged.length ?? 0);
</script>

<BaseModal {isOpen} title="Replace in {values.length} value{values.length === 1 ? '' : 's'}" size="lg" on:close={onClose}>
	<div class="flex flex-col gap-4 p-4 md:p-6" data-replace-modal>
		<div class="flex items-center gap-2 flex-wrap text-xs text-fg-muted">
			<span>Find</span>
			<code class="font-mono px-1.5 py-0.5 rounded bg-surface-2 border border-line text-fg">{filters.query.trim()}</code>
			<Badge size="sm">{filters.mode}</Badge>
			{#if filters.caseSensitive}<Badge size="sm">match case</Badge>{/if}
		</div>

		<div>
			<label class="label" for="phrasebook-replace-with">Replace with</label>
			<Input
				id="phrasebook-replace-with"
				class="font-mono text-sm"
				placeholder="Replacement text"
				data-replace-input
				bind:value={replacement}
				oninput={schedulePreview}
				disabled={applying}
			/>
			{#if filters.mode === 'regex'}
				<p class="mt-1 text-xs text-fg-subtle font-mono">Groups: \1, \g&lt;name&gt;</p>
			{/if}
		</div>

		<div class="flex items-center gap-4 text-xs text-fg-muted">
			<span class="label mb-0">Fields</span>
			<label class="flex items-center gap-1.5 cursor-pointer select-none">
				<input type="checkbox" class="accent-accent" bind:checked={inLabel} onchange={loadPreview} data-replace-field="label" />
				Label
			</label>
			<label class="flex items-center gap-1.5 cursor-pointer select-none">
				<input type="checkbox" class="accent-accent" bind:checked={inValue} onchange={loadPreview} data-replace-field="value" />
				Value
			</label>
		</div>

		{#if error}
			<p class="text-xs text-danger" role="alert" data-replace-error>{error}</p>
		{/if}

		<div class="rounded-lg border border-line bg-surface-2/40 max-h-[40vh] overflow-y-auto" data-replace-preview>
			{#if previewing && !preview}
				<div class="flex items-center justify-center py-8"><Spinner size="sm" /></div>
			{:else if preview && preview.items.length > 0}
				<ul class="divide-y divide-line">
					{#each preview.items as item (item.id + item.field)}
						{@const diff = diffSegments(item.before, item.after)}
						<li class="px-3 py-2 text-xs">
							<div class="flex items-center gap-2 mb-1 text-fg-muted">
								<span class="truncate text-fg">{labelsById.get(item.id) ?? item.id}</span>
								<Badge size="sm">{item.field}</Badge>
							</div>
							<div class="font-mono whitespace-pre-wrap break-words text-fg-muted">
								<span>{diff.prefix}</span><span class="text-danger line-through">{diff.removed}</span><span class="bg-success/15 text-success">{diff.added}</span><span>{diff.suffix}</span>
							</div>
						</li>
					{/each}
				</ul>
			{:else if preview}
				<p class="px-3 py-6 text-center text-xs text-fg-subtle">Nothing would change</p>
			{/if}
		</div>

		{#if preview}
			<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted tabular-nums" data-replace-count>
				{changed} will change · {unchanged} unchanged
			</p>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 w-full">
			<Button variant="ghost" size="sm" onclick={onClose} disabled={applying}>Cancel</Button>
			<Button
				variant="primary"
				size="sm"
				loading={applying}
				disabled={previewing || changed === 0}
				onclick={apply}
			>
				Apply
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
