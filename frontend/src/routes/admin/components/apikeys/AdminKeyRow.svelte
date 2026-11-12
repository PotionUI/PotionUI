<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { Badge, IconButton } from '$lib/components/ui';
	import type { AdminApiKey } from '$lib/services/api/apiKeys';
	import { keyFingerprint } from '$lib/components/apikeys/apiKeyView';
	import OwnerAvatar from './OwnerAvatar.svelte';
	import { ADMIN_KEY_GRID } from './adminKeyColumns';
	import { adminKeyBadge, adminNeverExpires, canDoText, expiresText, lastUsedText, ownerName, revokable } from './adminKeysView';

	let {
		apiKey,
		revoking = false,
		onOpen,
		onRevoke
	}: {
		apiKey: AdminApiKey;
		revoking?: boolean;
		onOpen: (key: AdminApiKey) => void;
		onRevoke: (key: AdminApiKey) => void;
	} = $props();

	const owner = $derived(ownerName(apiKey));
	const badge = $derived(adminKeyBadge(apiKey));
	const inactive = $derived(!revokable(apiKey));
</script>

<div class="flex items-stretch hover:bg-surface-2/50" data-admin-key={apiKey.id} data-status={apiKey.status}>
	<button
		type="button"
		class="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)] gap-x-4 gap-y-0.5 px-4 py-2.5 text-left focus-visible:outline-none focus-visible:bg-surface-2 lg:items-center {ADMIN_KEY_GRID} {inactive ? 'opacity-50' : ''}"
		aria-label="Open {apiKey.name} for {owner}"
		onclick={() => onOpen(apiKey)}
	>
		<span class="flex min-w-0 items-center gap-2.5">
			<OwnerAvatar name={owner} />
			<span class="truncate text-sm font-medium text-fg">{owner}</span>
		</span>
		<span class="min-w-0">
			<span class="flex min-w-0 items-center gap-2">
				<span class="truncate text-sm text-fg" use:tooltip={{ text: apiKey.name, truncateOnly: true }}>{apiKey.name}</span>
				{#if badge}<Badge variant={badge.variant} size="sm" class="flex-shrink-0 lg:hidden">{badge.label}</Badge>{/if}
			</span>
			<span class="block truncate font-mono text-sm tabular-nums text-fg-subtle">{keyFingerprint(apiKey)}</span>
		</span>
		<span class="text-sm text-fg-muted">{canDoText(apiKey)}</span>
		<span class="font-mono text-sm tabular-nums text-fg-muted">
			<span class="mr-1.5 text-fg-subtle lg:hidden">Used</span>{lastUsedText(apiKey)}
		</span>
		<span class="font-mono text-sm tabular-nums {adminNeverExpires(apiKey) ? 'text-warning' : 'text-fg-muted'}" data-expires={adminNeverExpires(apiKey) ? 'uncapped' : 'set'}>
			<span class="mr-1.5 text-fg-subtle lg:hidden">Expires</span>{expiresText(apiKey)}
		</span>
		<span class="hidden lg:block">
			{#if badge}<Badge variant={badge.variant} size="sm">{badge.label}</Badge>{/if}
		</span>
	</button>
	<div class="flex w-11 flex-shrink-0 items-center justify-center">
		{#if !inactive}
			<IconButton icon="ban" label="Revoke key" size="sm" loading={revoking} class="hover:!text-danger" onclick={() => onRevoke(apiKey)} />
		{/if}
	</div>
</div>
