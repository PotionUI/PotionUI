<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DetailFooter } from '$lib/components/detail';
	import { Badge, Button, IconButton, Input } from '$lib/components/ui';
	import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
	import { PRESET_COLORS } from '$lib/types/segments';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { sectionHref } from '../library/librarySection';

	const LIST_LIMIT = 10;

	let {
		mode,
		category,
		name = $bindable(),
		color = $bindable(),
		description = $bindable(),
		segments,
		saving,
		dirtyCount,
		onBack,
		onSave,
		onDiscard,
		onDelete,
		newSegmentHref,
		filteredSegmentsHref
	}: {
		mode: 'create' | 'edit';
		category: SegmentCategory | null;
		name: string;
		color: string;
		description: string;
		segments: SavedSegment[];
		saving: boolean;
		dirtyCount: number;
		onBack: () => void;
		onSave: () => void;
		onDiscard: () => void;
		onDelete: () => void;
		newSegmentHref: string;
		filteredSegmentsHref: string;
	} = $props();

	const title = $derived(mode === 'create' ? 'New category' : category?.name || 'Untitled');
	const shown = $derived(segments.slice(0, LIST_LIMIT));
	const hidden = $derived(Math.max(0, segments.length - LIST_LIMIT));
</script>

<div class="flex h-full flex-col">
	<DetailHeader {title} icon="folder" backLabel="Categories" {onBack}>
		{#snippet chips()}
			<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {color}"></span>
			{#if mode === 'edit'}
				<Badge size="sm">
					<span class="font-mono tabular-nums">{segments.length}</span>
					segment{segments.length === 1 ? '' : 's'}
				</Badge>
			{/if}
		{/snippet}
		{#snippet subtitle()}
			{#if mode === 'edit' && category?.created_at}
				<span>created {timeAgo(category.created_at)}</span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if mode === 'edit'}
				<Tooltip text="Delete category">
					<IconButton icon="trash" label="Delete category" onclick={onDelete} />
				</Tooltip>
			{/if}
			<Button size="sm" variant="secondary" icon="plus" href={newSegmentHref}>New segment here</Button>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Category details">
					<div class="space-y-3">
						<label>
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Name <span class="text-danger">*</span>
							</span>
							<Input class="text-sm" bind:value={name} placeholder="Category name" />
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
									{#each PRESET_COLORS as option (option.value)}
										<option value={option.value}>{option.name}</option>
									{/each}
								</select>
							</div>
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
					</div>
				</DetailSection>

				{#if mode === 'edit'}
					<DetailSection label="Segments in this category">
						{#if segments.length === 0}
							<p class="text-xs text-fg-subtle">No segments in this category yet.</p>
						{:else}
							<ul class="-mx-2 space-y-0.5">
								{#each shown as segment (segment.id)}
									<li>
										<a
											href={sectionHref('segments', { id: segment.id })}
											class="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-fg transition-colors hover:bg-surface-2"
										>
											<span
												class="h-2.5 w-2.5 flex-shrink-0 rounded-full"
												style="background: {segment.effective_color || color}"
											></span>
											<span class="min-w-0 flex-1 truncate">{segment.name}</span>
											<Badge size="sm" variant={segment.type === 'break' ? 'warning' : 'neutral'}>
												{segment.type === 'break' ? 'break' : 'content'}
											</Badge>
										</a>
									</li>
								{/each}
							</ul>
							<div class="mt-3 flex items-center gap-2">
								{#if hidden > 0}
									<span class="font-mono text-xs tabular-nums text-fg-subtle">+{hidden} more ·</span>
								{/if}
								<a href={filteredSegmentsHref} class="inline-flex items-center gap-1 text-xs text-signal hover:underline">
									Open in Segments filtered by this category
									<Icon name="arrow-right" className="h-3 w-3" />
								</a>
							</div>
						{/if}
					</DetailSection>
				{/if}
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Deleting">
					<div class="rounded border border-info/25 bg-info/5 p-3 text-xs text-fg-muted">
						<div class="flex items-start gap-2">
							<Icon name="info" className="mt-0.5 h-4 w-4 flex-shrink-0 text-info" />
							<p>
								A category cannot be deleted while segments reference it. Deleting a category with segments
								first offers to move them to another category. Template slots are independent of categories.
							</p>
						</div>
					</div>
				</DetailSection>
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter {dirtyCount}>
		<Button size="sm" variant="secondary" onclick={onDiscard}>Discard</Button>
		<Button size="sm" variant="primary" loading={saving} disabled={!name.trim()} onclick={onSave}>
			{mode === 'create' ? 'Create' : 'Save'}
		</Button>
	</DetailFooter>
</div>
