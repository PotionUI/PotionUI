<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Card, Alert } from '$lib/components/ui';
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
</script>

<Card padding="md" class="flex h-full flex-col gap-3">
	<div class="flex items-start gap-3">
		<div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-signal/10 text-signal">
			<Icon name={template.icon || 'bolt'} className="h-5 w-5" />
		</div>
		<div class="min-w-0 flex-1">
			<h3 class="text-sm font-semibold text-fg">{template.title}</h3>
			<p class="mt-0.5 text-xs text-fg-muted line-clamp-3">{template.description}</p>
		</div>
	</div>

	<div class="flex flex-wrap items-center gap-1.5">
		<Badge variant={template.source === 'core' ? 'signal' : 'info'} size="sm">
			{template.source_name}
		</Badge>
		<Badge variant="neutral" size="sm">{template.category}</Badge>
		{#each template.tags.slice(0, 3) as tag (tag)}
			<span class="rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-fg-subtle">{tag}</span>
		{/each}
	</div>

	{#if template.missing_node_types.length > 0}
		<Alert variant="warning" density="compact" title="Missing requirements">
			<span class="break-words font-mono text-2xs">{template.missing_node_types.join(', ')}</span>
		</Alert>
	{:else}
		<p class="text-2xs text-fg-subtle">
			Creates a disabled automation for you to review and configure.
		</p>
	{/if}

	<div class="mt-auto flex items-center justify-between gap-2 pt-1">
		<span class="font-mono text-2xs text-fg-subtle">
			{template.node_types.length} node {template.node_types.length === 1 ? 'type' : 'types'}
		</span>
		<Button
			variant="secondary"
			size="sm"
			icon="plus"
			loading={loading}
			disabled={!template.available}
			title={template.available ? 'Create a disabled automation from this template' : 'Install the missing node types first'}
			onclick={() => onUse(template)}
		>
			Use template
		</Button>
	</div>
</Card>
