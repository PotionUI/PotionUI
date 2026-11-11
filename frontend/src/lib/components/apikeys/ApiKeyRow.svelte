<script lang="ts">
	import { Badge, IconButton } from '$lib/components/ui';
	import tooltip from '$lib/actions/tooltip';
	import type { ApiKey } from '$lib/services/api/apiKeys';
	import { keyBadge, keyLine } from './apiKeyView';

	let {
		apiKey,
		mcpPaused = false,
		revoking = false,
		onEdit,
		onRevoke
	}: {
		apiKey: ApiKey;
		mcpPaused?: boolean;
		revoking?: boolean;
		onEdit: (key: ApiKey) => void;
		onRevoke: (key: ApiKey) => void;
	} = $props();

	const inactive = $derived(apiKey.status !== 'active');
	const badge = $derived(keyBadge(apiKey));
	const line = $derived(keyLine(apiKey, { mcpPaused }));
</script>

<div
	class="flex items-center justify-between gap-3 px-3 py-2.5 {inactive ? 'opacity-50' : ''}"
	data-key-row={apiKey.id}
	data-status={apiKey.status}
>
	<div class="min-w-0 flex-1">
		<div class="flex min-w-0 items-center gap-2">
			<p class="min-w-0 truncate text-sm text-fg" use:tooltip={{ text: apiKey.name, truncateOnly: true }}>{apiKey.name}</p>
			{#if badge}
				<Badge variant={badge.variant} size="sm" class="flex-shrink-0">{badge.label}</Badge>
			{/if}
		</div>
		<p class="font-mono text-sm tabular-nums text-fg-subtle [overflow-wrap:anywhere]">{line}</p>
	</div>
	{#if !inactive}
		<div class="flex flex-shrink-0 items-center gap-0.5">
			<IconButton icon="edit" label="Edit key" size="sm" onclick={() => onEdit(apiKey)} />
			<IconButton icon="ban" label="Revoke key" size="sm" loading={revoking} class="hover:!text-danger" onclick={() => onRevoke(apiKey)} />
		</div>
	{/if}
</div>
