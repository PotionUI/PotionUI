<script lang="ts">
	import TagSelector from '$lib/components/TagSelector.svelte';
	import { Badge, IconButton } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';

	let {
		tags,
		onChange
	}: {
		tags: Array<{ id: string; name: string }>;
		onChange: (tagIds: string[]) => void;
	} = $props();

	const tagIds = $derived(tags.map((tag) => tag.id));

	function remove(tagId: string) {
		onChange(tagIds.filter((id) => id !== tagId));
	}
</script>

<DetailSection label="Tags" icon="tag">
	<div class="flex flex-wrap items-center gap-2" data-testid="model-tags">
		{#each tags as tag (tag.id)}
			<Badge variant="neutral">
				{tag.name}
				<IconButton
					icon="close"
					label={`Remove ${tag.name}`}
					size="xs"
					onclick={() => remove(tag.id)}
				/>
			</Badge>
		{/each}
		<TagSelector
			selectedTagIds={tagIds}
			tagType="MODEL"
			placeholder="Add tag"
			triggerLabel="Add tag"
			allowCreate={true}
			compact={true}
			on:change={(event) => onChange(event.detail)}
		/>
	</div>
	<p class="mt-2 text-xs text-fg-subtle">Presets use model tags to choose which models they offer.</p>
</DetailSection>
