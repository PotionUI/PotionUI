<script lang="ts">
	import InlineChipEditor from '$lib/components/InlineChipEditor.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DetailFooter, KVGrid, KVItem } from '$lib/components/detail';
	import { Badge, Button, IconButton, Input } from '$lib/components/ui';
	import type { ChipData, RichSegmentType, SavedSegment, SegmentCategory } from '$lib/types/segments';
	import { PRESET_COLORS } from '$lib/types/segments';
	import { timeAgo } from '$lib/utils/relativeTime';

	const NAME_FIELD_ID = 'segment-detail-name-field';

	let {
		mode,
		segment,
		categories,
		categorySegmentCount,
		name = $bindable(),
		categoryId = $bindable(),
		type = $bindable(),
		content = $bindable(),
		chips = $bindable(),
		enabled = $bindable(),
		color = $bindable(),
		description = $bindable(),
		tagsText = $bindable(),
		saving,
		dirtyCount,
		onBack,
		onSave,
		onDiscard,
		onDelete,
		onDuplicate,
		onInsert
	}: {
		mode: 'create' | 'edit';
		segment: SavedSegment | null;
		categories: readonly SegmentCategory[];
		categorySegmentCount: number;
		name: string;
		categoryId: string;
		type: RichSegmentType;
		content: string;
		chips: Record<string, ChipData>;
		enabled: boolean;
		color: string;
		description: string;
		tagsText: string;
		saving: boolean;
		dirtyCount: number;
		onBack: () => void;
		onSave: () => void;
		onDiscard: () => void;
		onDelete: () => void;
		onDuplicate: () => void;
		onInsert: () => void;
	} = $props();

	const title = $derived(mode === 'create' ? 'New segment' : name.trim() || segment?.name || 'Untitled');
	const category = $derived(categories.find((entry) => entry.id === categoryId) ?? null);
	const effectiveColor = $derived(color || category?.color || '#3B82F6');
	const canSave = $derived(!!name.trim() && !!categoryId);
	const canInsert = $derived(type === 'break' || !!content.trim());
</script>

<div class="flex h-full flex-col">
	<DetailHeader {title} icon="list" backLabel="Segments" {onBack}>
		{#snippet chips()}
			<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {effectiveColor}"></span>
			<Badge size="sm" variant={type === 'break' ? 'warning' : 'neutral'}>{type}</Badge>
			{#if category}
				<Badge size="sm">{category.name}</Badge>
			{/if}
		{/snippet}
		{#snippet subtitle()}
			{#if mode === 'edit' && segment?.created_at}
				<span>created {timeAgo(segment.created_at)}</span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if mode === 'edit'}
				<Tooltip text="Duplicate segment">
					<IconButton icon="copy" label="Duplicate segment" onclick={onDuplicate} />
				</Tooltip>
				<Tooltip text="Delete segment">
					<IconButton icon="trash" label="Delete segment" onclick={onDelete} />
				</Tooltip>
			{/if}
			<Button size="sm" variant="primary" disabled={!canInsert} onclick={onInsert}>Insert into prompt</Button>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Segment details">
					<div class="grid gap-3 sm:grid-cols-2">
						<label>
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Name <span class="text-danger">*</span>
							</span>
							<Input id={NAME_FIELD_ID} class="text-sm" bind:value={name} placeholder="Segment name" />
						</label>
						<label>
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Category <span class="text-danger">*</span>
							</span>
							<select class="input text-sm" bind:value={categoryId}>
								{#if categories.length === 0}
									<option value="">No categories yet</option>
								{/if}
								{#each categories as entry (entry.id)}
									<option value={entry.id}>{entry.name}</option>
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
									{#each PRESET_COLORS as option (option.value)}
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
								placeholder="What this segment is for"
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
				</DetailSection>

				<DetailSection label="Content" padded={type === 'break'}>
					{#if type === 'content'}
						<InlineChipEditor
							value={content}
							{chips}
							borderless={true}
							on:change={(event) => {
								content = event.detail.value;
								chips = event.detail.chips;
							}}
						/>
					{:else}
						<div class="rounded-lg border border-dashed border-line-strong px-3">
							<div class="flex items-center gap-3 py-3 text-xs uppercase tracking-wide text-fg-muted">
								<span class="h-px flex-1 bg-line"></span>
								Prompt break
								<span class="h-px flex-1 bg-line"></span>
							</div>
						</div>
					{/if}
				</DetailSection>
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Category">
					{#if category}
						<KVGrid>
							<KVItem label="Name" full>{category.name}</KVItem>
							<KVItem label="Segments" mono>{categorySegmentCount}</KVItem>
							<KVItem label="Color">
								<span class="inline-flex items-center gap-2">
									<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {effectiveColor}"></span>
									<span class="text-xs text-fg-muted">{color ? 'override' : 'inherited'}</span>
								</span>
							</KVItem>
						</KVGrid>
					{:else}
						<p class="text-xs text-fg-subtle">Pick a category to see its details.</p>
					{/if}
				</DetailSection>
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter {dirtyCount}>
		<Button size="sm" variant="secondary" onclick={onDiscard}>Discard</Button>
		<Button size="sm" variant="primary" loading={saving} disabled={!canSave} onclick={onSave}>
			{mode === 'create' ? 'Create' : 'Save'}
		</Button>
	</DetailFooter>
</div>
