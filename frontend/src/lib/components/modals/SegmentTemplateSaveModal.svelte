<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';
	import { Input } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import type { Segment } from '$lib/types/segments';
	import { flattenRichSegments, toRichSegment } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';

	// Forks the editor's current cards into the user's own Segment Template
	// library. A preset-declared template has no database row, so this is the
	// only way to keep an edited copy of one.
	let {
		isOpen,
		segments,
		onClose,
		onSaved
	}: {
		isOpen: boolean;
		segments: Segment[];
		onClose: () => void;
		onSaved: () => void;
	} = $props();

	let name = $state('');
	let description = $state('');
	let saving = $state(false);
	const settlementGate = createConfirmSettlementGate();

	let preview = $derived(flattenRichSegments(segments));
	let sourceNames = $derived([...new Set(segments.map((s) => s.template?.name).filter(Boolean))]);

	$effect(() => {
		if (!isOpen) return;
		untrack(() => {
			name = sourceNames.length === 1 ? (sourceNames[0] as string) : '';
			description = '';
			settlementGate.reset();
		});
	});

	async function save() {
		saving = true;
		try {
			const response = await api.createSegmentTemplate({
				name: name.trim(),
				description: description.trim() || null,
				tags: [],
				segments: segments.map(toRichSegment)
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success('Segment Template saved');
			onSaved();
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Segment Template');
			settlementGate.reset();
		} finally {
			saving = false;
		}
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !!name.trim() && !saving, save);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen || saving) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	{isOpen}
	title="Save as Segment Template"
	size="md"
	closeable={!saving}
	handleEscapeKey={false}
	on:close={handleCancel}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="layout-template" className="h-5 w-5 text-fg-muted" />
	</svelte:fragment>

	<div class="space-y-4 p-4 sm:p-6">
		<div>
			<label class="mb-1.5 block text-sm font-medium text-fg" for="segment-template-name">Name</label>
			<Input id="segment-template-name" bind:value={name} placeholder="How you will find it later" />
		</div>

		<div>
			<label class="mb-1.5 block text-sm font-medium text-fg" for="segment-template-description">
				Description <span class="font-normal text-fg-subtle">(optional)</span>
			</label>
			<Input id="segment-template-description" bind:value={description} placeholder="What this structure is for" />
		</div>

		<div>
			<span class="mb-1.5 block text-sm font-medium text-fg">Composition</span>
			<div class="max-h-36 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3 text-sm text-fg-muted">
				{preview || 'Blank segments'}
			</div>
			<p class="mt-1.5 text-xs text-fg-subtle">
				{segments.length} segment{segments.length === 1 ? '' : 's'}, disabled ones included.
			</p>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Save Template"
			busy={saving}
			confirmDisabled={!name.trim()}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
