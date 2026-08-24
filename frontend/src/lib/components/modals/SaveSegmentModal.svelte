<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { Segment, SegmentCategory } from '$lib/types/segments';
	import { toRichSegment } from '$lib/utils/richSegments';
	import { toasts } from '$lib/stores/toast';
	import BaseModal from './BaseModal.svelte';
	import { Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let segment: Segment;
	const dispatch = createEventDispatcher<{ close: void; saved: void }>();
	let categories: SegmentCategory[] = [];
	let name = '';
	let categoryId = '';
	let description = '';
	let color = '';
	let tags = '';
	let saving = false;
	let previousOpen = false;

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) initialize();
	}

	async function initialize() {
		name = segment.name || segment.title || '';
		description = segment.description || '';
		color = segment.color || '';
		tags = '';
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
			const rich = toRichSegment({ ...segment, name: name.trim(), description, color });
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
</script>

<BaseModal {isOpen} title="Save as Segment" sizeClass="md:max-w-lg md:w-full" on:close={() => dispatch('close')}>
	<svelte:fragment slot="headerIcon"><Icon name="save" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-4 p-4 sm:p-6">
		<label class="block text-sm font-medium text-fg" for="saved-segment-name">Name <span class="text-danger">*</span></label>
		<input id="saved-segment-name" class="input -mt-3 w-full" bind:value={name} placeholder="Required library name" />
		<label class="block text-sm font-medium text-fg" for="saved-segment-category">Category <span class="text-danger">*</span></label>
		<select id="saved-segment-category" class="input -mt-3 w-full" bind:value={categoryId}><option value="">Select a category</option>{#each categories as category}<option value={category.id}>{category.name}</option>{/each}</select>
		<label class="block text-sm font-medium text-fg" for="saved-segment-description">Description</label>
		<textarea id="saved-segment-description" class="input -mt-3 w-full" rows="2" bind:value={description}></textarea>
		<div class="grid grid-cols-[7rem_1fr] gap-3"><label class="text-sm font-medium text-fg" for="saved-segment-color">Color</label><label class="text-sm font-medium text-fg" for="saved-segment-tags">Tags</label><input id="saved-segment-color" class="input w-full" bind:value={color} placeholder="Optional" /><input id="saved-segment-tags" class="input w-full" bind:value={tags} placeholder="comma, separated" /></div>
		<p class="text-xs text-fg-subtle">This creates a detached reusable card; later edits to either copy do not stay linked.</p>
	</div>
	<svelte:fragment slot="footer"><div class="flex justify-end gap-2 px-4 py-3 sm:px-6"><Button variant="secondary" onclick={() => dispatch('close')}>Cancel</Button><Button variant="primary" loading={saving} disabled={!name.trim() || !categoryId} onclick={save}>Save Segment</Button></div></svelte:fragment>
</BaseModal>
