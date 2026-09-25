<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { Segment, SegmentCategory } from '$lib/types/segments';
	import { toRichSegment } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';
	import { resolveResourceMarkers, textHasResourceMarkers, type PromptResourceSpec } from '$lib/utils/promptResources';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let segment: Segment;
	export let promptResources: PromptResourceSpec[] = [];
	export let resourceFieldValues: Record<string, unknown> = {};
	const dispatch = createEventDispatcher<{ close: void; saved: void }>();
	let categories: SegmentCategory[] = [];
	let name = '';
	let categoryId = '';
	let description = '';
	let color = '';
	let tags = '';
	let saving = false;
	let previousOpen = false;
	let convertReferences = true;
	const settlementGate = createConfirmSettlementGate();

	$: hasReferences = textHasResourceMarkers(segment?.content);

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) {
			initialize();
			settlementGate.reset();
		}
	}

	async function initialize() {
		name = segment.name || segment.title || '';
		description = segment.description || '';
		color = segment.color || '';
		tags = '';
		convertReferences = true;
		try {
			categories = (await api.listSegmentCategories()).data?.categories || [];
			categoryId = categories[0]?.id || '';
		} catch {
			categories = [];
		}
	}

	async function save() {
		if (!name.trim() || !categoryId) return;
		saving = true;
		try {
			const shouldConvert = hasReferences && convertReferences;
			const content = shouldConvert
				? resolveResourceMarkers(segment.content, promptResources, resourceFieldValues)
				: segment.content;
			const rich = toRichSegment({
				...segment,
				content,
				resources: shouldConvert ? {} : segment.resources,
				name: name.trim(),
				description,
				color
			});
			const response = await api.createSavedSegment({
				...rich,
				name: name.trim(),
				category_id: categoryId,
				color: color || null,
				description: description.trim() || null,
				tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean)
			});
			if (!response.success) throw new Error(response.error || 'Failed to save Segment');
			toasts.success('Segment saved to library');
			dispatch('saved');
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Segment');
		} finally {
			saving = false;
		}
	}

	function handleCancel() {
		settlementGate.settle(() => dispatch('close'));
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !saving && !!name.trim() && !!categoryId, save);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} title="Save as Segment" sizeClass="md:max-w-lg md:w-full" handleEscapeKey={false} on:close={handleCancel}>
	<svelte:fragment slot="headerIcon"><Icon name="save" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-4 p-4 sm:p-6">
		<label class="block text-sm font-medium text-fg" for="saved-segment-name">Name <span class="text-danger">*</span></label>
		<input id="saved-segment-name" class="input -mt-3 w-full" bind:value={name} placeholder="Required library name" />
		<label class="block text-sm font-medium text-fg" for="saved-segment-category">Category <span class="text-danger">*</span></label>
		<select id="saved-segment-category" class="input -mt-3 w-full" bind:value={categoryId}><option value="">Select a category</option>{#each categories as category}<option value={category.id}>{category.name}</option>{/each}</select>
		<label class="block text-sm font-medium text-fg" for="saved-segment-description">Description</label>
		<textarea id="saved-segment-description" class="input -mt-3 w-full" rows="2" bind:value={description}></textarea>
		<div class="grid grid-cols-[7rem_1fr] gap-3"><label class="text-sm font-medium text-fg" for="saved-segment-color">Color</label><label class="text-sm font-medium text-fg" for="saved-segment-tags">Tags</label><input id="saved-segment-color" class="input w-full" bind:value={color} placeholder="Optional" /><input id="saved-segment-tags" class="input w-full" bind:value={tags} placeholder="comma, separated" /></div>
		{#if hasReferences}
			<div class="rounded-lg border border-warning/40 bg-warning/10 p-3 space-y-2">
				<p class="text-xs text-fg">
					This segment references media uploaded on this tab. Those references only resolve here — a copy saved to
					the library needs its own choice.
				</p>
				<label class="flex items-start gap-2 text-xs text-fg">
					<input type="radio" name="reference-handling" checked={convertReferences} on:change={() => (convertReferences = true)} class="mt-0.5" />
					<span>Convert to plain text now (e.g. "Picture 2") — always valid, never dangling</span>
				</label>
				<label class="flex items-start gap-2 text-xs text-fg">
					<input type="radio" name="reference-handling" checked={!convertReferences} on:change={() => (convertReferences = false)} class="mt-0.5" />
					<span>Keep the references — only works when applied on a tab with the same uploads</span>
				</label>
			</div>
		{/if}
		<p class="text-xs text-fg-subtle">This creates a detached reusable card; later edits to either copy do not stay linked.</p>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Save Segment"
			busy={saving}
			confirmDisabled={!name.trim() || !categoryId}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
