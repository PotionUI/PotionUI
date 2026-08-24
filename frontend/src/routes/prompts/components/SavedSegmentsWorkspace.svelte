<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import InlineChipEditor from '$lib/components/InlineChipEditor.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { MasterDetailLayout, DetailPane } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { Badge, Button, Card, Input } from '$lib/components/ui';
	import type { ChipData, SavedSegment, SegmentCategory } from '$lib/types/segments';
	import { PRESET_COLORS } from '$lib/types/segments';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';

	let categories: SegmentCategory[] = [];
	let segments: SavedSegment[] = [];
	let categoryFilter = '';
	let query = '';
	let selected: SavedSegment | null = null;
	let name = '';
	let categoryId = '';
	let type: 'content' | 'break' = 'content';
	let content = '';
	let chips: Record<string, ChipData> = {};
	let enabled = true;
	let color = '';
	let description = '';
	let tagsText = '';
	let loading = false;
	let saving = false;

	onMount(loadAll);
	$: effectiveColor = color || categories.find((category) => category.id === categoryId)?.color || '#3B82F6';
	$: normalizedQuery = query.trim().toLowerCase();
	$: filteredSegments = normalizedQuery
		? segments.filter((segment) => {
				const categoryName = categories.find((category) => category.id === segment.category_id)?.name || '';
				return [segment.name, segment.content, segment.description || '', ...(segment.tags || []), categoryName]
					.join(' ')
					.toLowerCase()
					.includes(normalizedQuery);
			})
		: segments;

	async function loadAll() {
		loading = true;
		try {
			const [categoryResponse, segmentResponse] = await Promise.all([
				api.listSegmentCategories(),
				api.listSavedSegments(categoryFilter || undefined)
			]);
			categories = categoryResponse.data?.categories || [];
			segments = segmentResponse.data?.segments || [];
			if (!categoryId && categories[0]) categoryId = categories[0].id;
		} catch {
			toasts.error('Failed to load saved Segments');
		} finally {
			loading = false;
		}
	}

	async function applyFilter() {
		loading = true;
		try {
			const response = await api.listSavedSegments(categoryFilter || undefined);
			segments = response.data?.segments || [];
		} catch {
			toasts.error('Failed to filter saved Segments');
		} finally {
			loading = false;
		}
	}

	function selectSegment(segment: SavedSegment) {
		selected = segment;
		name = segment.name;
		categoryId = segment.category_id;
		type = segment.type;
		content = segment.content;
		chips = structuredClone(segment.chips || {});
		enabled = segment.enabled;
		color = segment.color || '';
		description = segment.description || '';
		tagsText = (segment.tags || []).join(', ');
	}

	function newSegment() {
		selected = null;
		name = '';
		categoryId = categoryFilter || categories[0]?.id || '';
		type = 'content';
		content = '';
		chips = {};
		enabled = true;
		color = '';
		description = '';
		tagsText = '';
	}

	function resetSegment() {
		if (selected) selectSegment(selected);
		else newSegment();
	}

	async function save() {
		if (!name.trim() || !categoryId) {
			toasts.error('A name and category are required');
			return;
		}
		saving = true;
		const payload = {
			name: name.trim(),
			category_id: categoryId,
			type,
			content: type === 'break' ? '' : content,
			chips: type === 'break' ? {} : chips,
			enabled,
			color: color || null,
			description: description.trim() || null,
			tags: tagsText
				.split(',')
				.map((tag) => tag.trim())
				.filter(Boolean)
		};
		try {
			const response = selected
				? await api.updateSavedSegment(selected.id, payload)
				: await api.createSavedSegment(payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success(selected ? 'Segment updated' : 'Segment saved');
			await applyFilter();
			selectSegment(response.data);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Segment');
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (!selected) return;
		if (
			!(await confirmDialog({
				title: 'Delete',
				message: `Delete “${selected.name}”?`,
				variant: 'danger'
			}))
		)
			return;
		try {
			await api.deleteSavedSegment(selected.id);
			toasts.success('Segment deleted');
			newSegment();
			await applyFilter();
		} catch {
			toasts.error('Failed to delete Segment');
		}
	}
</script>

<div class="h-full">
	<MasterDetailLayout
		leftWidth={340}
		minWidth={260}
		maxWidth={480}
		storageKey="saved-segments-panel-width"
	>
		<svelte:fragment slot="list">
			<Pane
				label="Saved Segments"
				count={filteredSegments.length}
				searchable
				bind:search={query}
				searchPlaceholder="Search saved segments..."
				{loading}
				isEmpty={filteredSegments.length === 0}
			>
				{#snippet headerActions()}
					<Button variant="primary" size="xs" icon="plus" onclick={newSegment}>New Segment</Button>
				{/snippet}

				{#snippet filters()}
					<div class="border-b border-line p-3">
						<label>
							<span class="sr-only">Category</span>
							<select class="input py-1.5 text-xs" bind:value={categoryFilter} onchange={applyFilter}>
								<option value="">All categories</option>
								{#each categories as category}
									<option value={category.id}>{category.name}</option>
								{/each}
							</select>
						</label>
					</div>
				{/snippet}

				{#snippet empty()}
					<div class="px-4 py-10 text-center">
						<Icon name="list" className="mx-auto mb-3 h-9 w-9 text-fg-disabled" strokeWidth={1.5} />
						<p class="text-sm text-fg-muted">
							{query.trim() ? 'No saved Segments match your search' : 'No saved Segments in this category'}
						</p>
					</div>
				{/snippet}

				{#snippet children()}
					{#each filteredSegments as segment (segment.id)}
						{#snippet meta()}
							<div class="mt-2 flex flex-wrap gap-1.5">
								<Badge size="sm" variant={segment.type === 'break' ? 'warning' : 'neutral'}>
									{segment.type === 'break' ? 'Break' : 'Content'}
								</Badge>
								{#if !segment.enabled}<Badge size="sm">Disabled</Badge>{/if}
							</div>
						{/snippet}
						<PaneRow
							dot={segment.effective_color || '#3B82F6'}
							title={segment.name}
							subtitle={segment.type === 'break' ? 'Prompt break' : segment.content || 'Empty starter content'}
							selected={selected?.id === segment.id}
							onclick={() => selectSegment(segment)}
							{meta}
						/>
					{/each}
				{/snippet}
			</Pane>
		</svelte:fragment>

		<svelte:fragment slot="detail">
			<DetailPane
				title={selected ? 'Edit Saved Segment' : 'New Saved Segment'}
				showDelete={Boolean(selected)}
				showCancel={Boolean(selected)}
				saveLabel={selected ? 'Save changes' : 'Save Segment'}
				saveDisabled={!name.trim() || !categoryId}
				isLoading={saving}
				on:save={save}
				on:cancel={resetSegment}
				on:delete={remove}
			>
				<div class="space-y-4">
					<Card padding="sm">
						<h3 class="label mb-3">Segment details</h3>
						<div class="grid gap-3 sm:grid-cols-2">
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Name <span class="text-danger">*</span>
								</span>
								<Input class="text-sm" bind:value={name} placeholder="Segment name" />
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Category <span class="text-danger">*</span>
								</span>
								<select class="input text-sm" bind:value={categoryId}>
									{#each categories as category}
										<option value={category.id}>{category.name}</option>
									{/each}
								</select>
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">Card type</span>
								<select class="input text-sm" bind:value={type}>
									<option value="content">Content</option>
									<option value="break">Break</option>
								</select>
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">Color override</span>
								<div class="flex gap-2">
									<input
										type="color"
										class="h-10 w-12 flex-shrink-0 rounded border border-line-strong bg-surface-2 p-1"
										value={effectiveColor}
										oninput={(event) => (color = event.currentTarget.value)}
									/>
									<select class="input min-w-0 text-sm" bind:value={color}>
										<option value="">Use category color</option>
										{#each PRESET_COLORS as option}
											<option value={option.value}>{option.name}</option>
										{/each}
									</select>
								</div>
							</label>
						</div>

						<div class="mt-3 grid gap-3 sm:grid-cols-2">
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">Description</span>
								<textarea
									class="input resize-y text-sm"
									rows="2"
									bind:value={description}
									placeholder="What this Segment is for"
								></textarea>
							</label>
							<label>
								<span class="mb-1.5 block text-xs font-medium text-fg-muted">
									Tags <span class="font-normal text-fg-subtle">(comma separated)</span>
								</span>
								<Input class="text-sm" bind:value={tagsText} placeholder="portrait, lighting" />
							</label>
						</div>

						<label class="mt-3 flex items-center gap-2 text-sm text-fg">
							<input
								type="checkbox"
								class="h-4 w-4 rounded border-line-strong bg-surface-2 text-accent focus:ring-accent"
								bind:checked={enabled}
							/>
							Enabled when inserted
						</label>
					</Card>

					{#if type === 'content'}
						<Card padding="none" class="overflow-hidden shadow-none">
							<div class="border-b border-line px-3 py-2">
								<h3 class="label !mb-0">Starter content</h3>
							</div>
							<InlineChipEditor
								value={content}
								{chips}
								borderless={true}
								on:change={(event) => {
									content = event.detail.value;
									chips = event.detail.chips;
								}}
							/>
						</Card>
					{:else}
						<Card padding="sm" class="border-dashed shadow-none">
							<div class="flex items-center gap-3 py-3 text-xs uppercase tracking-wide text-fg-muted">
								<span class="h-px flex-1 bg-line"></span>
								Prompt break
								<span class="h-px flex-1 bg-line"></span>
							</div>
						</Card>
					{/if}
				</div>
			</DetailPane>
		</svelte:fragment>
	</MasterDetailLayout>
</div>
