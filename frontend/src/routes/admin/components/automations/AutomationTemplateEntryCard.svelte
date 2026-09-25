<script lang="ts">
	import { Badge, Button, Alert } from '$lib/components/ui';
	import LibraryEntryCard from '$lib/components/library/LibraryEntryCard.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { AutomationTemplate } from '$lib/types/automations';

	let {
		template,
		loading = false,
		onUse
	}: {
		template: AutomationTemplate;
		loading?: boolean;
		onUse: (template: AutomationTemplate) => void;
	} = $props();

	function use(event: MouseEvent) {
		event.stopPropagation();
		if (template.available && !loading) onUse(template);
	}
</script>

<LibraryEntryCard
	icon={template.icon || 'bolt'}
	name={template.title}
	description={template.description}
	onOpen={() => {
		if (template.available && !loading) onUse(template);
	}}
	ariaLabel={template.title}
>
	{#snippet topRight()}
		<Badge variant={template.source === 'core' ? 'signal' : 'info'} size="sm" class="flex-shrink-0">
			{template.source_name}
		</Badge>
	{/snippet}
	{#snippet details()}
		<div class="flex flex-wrap items-center gap-1.5">
			<Badge variant="neutral" size="sm">{template.category}</Badge>
			{#each template.tags.slice(0, 3) as tag (tag)}
				<span class="rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-fg-subtle">{tag}</span>
			{/each}
		</div>
		{#if template.missing_node_types.length > 0}
			<Alert variant="warning" density="compact" title="Missing requirements" class="mt-2">
				<span class="break-words font-mono text-2xs">{template.missing_node_types.join(', ')}</span>
			</Alert>
		{/if}
	{/snippet}
	{#snippet footer()}
		<span class="font-mono text-2xs text-fg-subtle">
			{template.node_types.length} node {template.node_types.length === 1 ? 'type' : 'types'}
		</span>
		<Tooltip
			text={template.available ? 'Create a disabled automation from this template' : 'Install the missing node types first'}
		>
			<Button
				variant="secondary"
				size="sm"
				icon="plus"
				class="ml-auto"
				loading={loading}
				disabled={!template.available}
				onclick={use}
			>
				Use template
			</Button>
		</Tooltip>
	{/snippet}
</LibraryEntryCard>
