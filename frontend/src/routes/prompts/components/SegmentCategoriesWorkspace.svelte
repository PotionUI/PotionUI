<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import Icon from '$lib/components/Icon.svelte';
	import { MasterDetailLayout, DetailPane } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { Button, Card, Input } from '$lib/components/ui';
	import type { SegmentCategory } from '$lib/types/segments';
	import { PRESET_COLORS } from '$lib/types/segments';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';

	let categories: SegmentCategory[] = [];
	let selected: SegmentCategory | null = null;
	let name = '';
	let description = '';
	let color = '#3B82F6';
	let query = '';
	let loading = false;
	let saving = false;

	$: normalizedQuery = query.trim().toLowerCase();
	$: filteredCategories = normalizedQuery
		? categories.filter((category) =>
				[category.name, category.description].join(' ').toLowerCase().includes(normalizedQuery)
			)
		: categories;

	onMount(load);

	async function load() {
		loading = true;
		try {
			categories = (await api.listSegmentCategories()).data?.categories || [];
		} catch {
			toasts.error('Failed to load Segment Categories');
		} finally {
			loading = false;
		}
	}

	function selectCategory(category: SegmentCategory) {
		selected = category;
		name = category.name;
		description = category.description;
		color = category.color;
	}

	function createNew() {
		selected = null;
		name = '';
		description = '';
		color = '#3B82F6';
	}

	function resetCategory() {
		if (selected) selectCategory(selected);
		else createNew();
	}

	async function save() {
		if (!name.trim()) {
			toasts.error('A category name is required');
			return;
		}
		saving = true;
		try {
			const payload = { name: name.trim(), description: description.trim(), color };
			const response = selected
				? await api.updateSegmentCategory(selected.id, payload)
				: await api.createSegmentCategory(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(selected ? 'Category updated' : 'Category created');
			await load();
			selectCategory(response.data);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save category');
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!selected) return;
		if (
			!(await confirmDialog({
				title: `Delete “${selected.name}”?`,
				message: 'Categories in use cannot be deleted.',
				variant: 'danger'
			}))
		)
			return;
		try {
			const response = await api.deleteSegmentCategory(selected.id);
			if (!response.success) throw new Error(response.error || response.message || 'Delete failed');
			toasts.success('Category deleted');
			createNew();
			await load();
		} catch (error) {
			toasts.error(
				error instanceof Error ? error.message : 'Remove saved Segments from this category first'
			);
		}
	}
</script>

<div class="h-full">
	<MasterDetailLayout
		leftWidth={320}
		minWidth={240}
		maxWidth={440}
		storageKey="segment-categories-panel-width"
	>
		<svelte:fragment slot="list">
			<Pane
				label="Segment Categories"
				count={filteredCategories.length}
				searchable
				bind:search={query}
				searchPlaceholder="Search Segment Categories..."
				{loading}
				isEmpty={filteredCategories.length === 0}
			>
				{#snippet headerActions()}
					<Button variant="primary" size="xs" icon="plus" onclick={createNew}>New Category</Button>
				{/snippet}

				{#snippet empty()}
					<div class="px-4 py-10 text-center">
						<Icon name="folder" className="mx-auto mb-3 h-9 w-9 text-fg-disabled" strokeWidth={1.5} />
						<p class="text-sm text-fg-muted">
							{query.trim() ? 'No Segment Categories match your search' : 'No Segment Categories yet'}
						</p>
					</div>
				{/snippet}

				{#snippet children()}
					{#each filteredCategories as category (category.id)}
						<PaneRow
							dot={category.color}
							title={category.name}
							subtitle={category.description || 'No description'}
							selected={selected?.id === category.id}
							onclick={() => selectCategory(category)}
						/>
					{/each}
				{/snippet}
			</Pane>
		</svelte:fragment>

		<svelte:fragment slot="detail">
			<DetailPane
				title={selected ? 'Edit Segment Category' : 'New Segment Category'}
				showDelete={Boolean(selected)}
				showCancel={Boolean(selected)}
				saveLabel={selected ? 'Save changes' : 'Create category'}
				saveDisabled={!name.trim()}
				isLoading={saving}
				on:save={save}
				on:cancel={resetCategory}
				on:delete={remove}
			>
				<div class="space-y-4">
					<Card padding="sm">
						<h3 class="label mb-3">Category details</h3>
						<div class="space-y-3">
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Name <span class="text-danger">*</span>
								</span>
								<Input class="text-sm" bind:value={name} placeholder="Category name" />
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">Description</span>
								<textarea
									class="input resize-y text-sm"
									rows="3"
									bind:value={description}
									placeholder="How this category is used"
								></textarea>
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">Color</span>
								<div class="flex gap-2">
									<input
										type="color"
										class="h-10 w-14 flex-shrink-0 rounded border border-line-strong bg-surface-2 p-1"
										bind:value={color}
									/>
									<select class="input min-w-0 text-sm" bind:value={color}>
										{#each PRESET_COLORS as option}
											<option value={option.value}>{option.name}</option>
										{/each}
									</select>
								</div>
							</label>
						</div>
					</Card>

					<Card padding="sm" class="border-info/25 bg-info/5 text-xs text-fg-muted shadow-none">
						<div class="flex items-start gap-2">
							<Icon name="info" className="mt-0.5 h-4 w-4 flex-shrink-0 text-info" />
							<p>
								A category cannot be deleted while saved Segments reference it. Template slots are
								independent of categories.
							</p>
						</div>
					</Card>
				</div>
			</DetailPane>
		</svelte:fragment>
	</MasterDetailLayout>
</div>
