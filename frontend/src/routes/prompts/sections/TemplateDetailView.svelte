<script lang="ts">
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DetailFooter, KVGrid, KVItem } from '$lib/components/detail';
	import { Badge, Button, IconButton, Input } from '$lib/components/ui';
	import type { Segment, SegmentTemplate } from '$lib/types/segments';
	import { flattenRichSegments, hasMeaningfulSegments, isSegmentEnabled } from '$lib/utils/richSegments';
	import { timeAgo } from '$lib/utils/relativeTime';

	let {
		mode,
		template,
		name = $bindable(),
		description = $bindable(),
		tagsText = $bindable(),
		editorSegments = $bindable(),
		saving,
		dirtyCount,
		onBack,
		onSave,
		onDiscard,
		onDelete,
		onDuplicate,
		onApply
	}: {
		mode: 'create' | 'edit';
		template: SegmentTemplate | null;
		name: string;
		description: string;
		tagsText: string;
		editorSegments: Segment[];
		saving: boolean;
		dirtyCount: number;
		onBack: () => void;
		onSave: () => void;
		onDiscard: () => void;
		onDelete: () => void;
		onDuplicate: () => void;
		onApply: () => void;
	} = $props();

	const title = $derived(mode === 'create' ? 'New template' : template?.name || 'Untitled');
	const slotCount = $derived(editorSegments.length);
	const enabledCount = $derived(editorSegments.filter((segment) => isSegmentEnabled(segment)).length);
	const preview = $derived(flattenRichSegments(editorSegments));
</script>

<div class="flex h-full flex-col">
	<DetailHeader {title} icon="layout-template" backLabel="Templates" {onBack}>
		{#snippet chips()}
			<Badge size="sm" variant="signal">
				<span class="font-mono tabular-nums">{slotCount}</span>
				slot{slotCount === 1 ? '' : 's'}
			</Badge>
		{/snippet}
		{#snippet subtitle()}
			{#if mode === 'edit' && template?.created_at}
				<span>created {timeAgo(template.created_at)}</span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if mode === 'edit'}
				<Tooltip text="Duplicate template">
					<IconButton icon="copy" label="Duplicate template" onclick={onDuplicate} />
				</Tooltip>
				<Tooltip text="Delete template">
					<IconButton icon="trash" label="Delete template" onclick={onDelete} />
				</Tooltip>
			{/if}
			<Button size="sm" variant="primary" onclick={onApply}>Apply to prompt</Button>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Template details">
					<div class="grid gap-3 sm:grid-cols-2">
						<label>
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Name <span class="text-danger">*</span>
							</span>
							<Input class="text-sm" bind:value={name} placeholder="Template name" />
						</label>
						<label>
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Tags <span class="font-normal text-fg-subtle">(comma separated)</span>
							</span>
							<Input class="text-sm" bind:value={tagsText} placeholder="portrait, lighting" />
						</label>
					</div>
					<label class="mt-3 block">
						<span class="mb-1.5 block text-xs font-medium text-fg-muted">Description</span>
						<textarea
							class="input resize-y text-sm"
							rows="2"
							bind:value={description}
							placeholder="What this layout is for"
						></textarea>
					</label>
				</DetailSection>

				<SegmentedPromptEditor
					segments={editorSegments}
					label="Template slots"
					compact
					showLibraryActions={false}
					on:segmentsChange={(event) => (editorSegments = event.detail)}
				/>
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Preview">
					{#if preview}
						<p class="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-fg-muted">{preview}</p>
					{:else}
						<p class="text-xs text-fg-subtle">Empty template</p>
					{/if}
				</DetailSection>

				<DetailSection label="Slots">
					<KVGrid>
						<KVItem label="Slots" mono>{slotCount}</KVItem>
						<KVItem label="Enabled" mono>{enabledCount}</KVItem>
					</KVGrid>
				</DetailSection>
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter {dirtyCount} {mode} {saving} canSave={!!name.trim() && hasMeaningfulSegments(editorSegments)} {onSave} {onDiscard} />
</div>
