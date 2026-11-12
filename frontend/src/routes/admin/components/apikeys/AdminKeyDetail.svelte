<script lang="ts">
	import { onMount } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import focusTrap from '$lib/actions/focusTrap';
	import { api } from '$lib/services/api/index';
	import type { AdminApiKey } from '$lib/services/api/apiKeys';
	import { Badge, Button, IconButton, Spinner } from '$lib/components/ui';
	import { keyFingerprint } from '$lib/components/apikeys/apiKeyView';
	import type { ActivityLine } from '$lib/components/apikeys/apiKeyEvents';
	import OwnerAvatar from './OwnerAvatar.svelte';
	import { adminKeyBadge, adminNeverExpires, canDoText, describeAdminEvents, expiresText, formatCreated, lastUsedText, ownerName, revokable } from './adminKeysView';

	let {
		apiKey,
		revoking = false,
		onClose,
		onRevoke
	}: {
		apiKey: AdminApiKey;
		revoking?: boolean;
		onClose: () => void;
		onRevoke: (key: AdminApiKey) => void;
	} = $props();

	let lines = $state<ActivityLine[]>([]);
	let loading = $state(true);
	let failed = $state(false);

	const owner = $derived(ownerName(apiKey));
	const badge = $derived(adminKeyBadge(apiKey));

	async function load() {
		loading = true;
		failed = false;
		try {
			const response = await api.listAdminApiKeyEvents(apiKey.id);
			if (response.success && response.data) lines = describeAdminEvents(response.data, owner);
			else failed = true;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<div
	use:portal
	use:overlayLayer
	use:dismiss={{ onDismiss: onClose, outside: false }}
	class="fixed inset-0 z-overlay"
	data-testid="admin-key-detail"
>
	<button type="button" class="absolute inset-0 h-full w-full cursor-default bg-black/40" aria-label="Close key details" tabindex="-1" onclick={onClose}></button>
	<div
		role="dialog"
		aria-modal="true"
		aria-label="Key details"
		use:focusTrap
		class="absolute inset-y-0 right-0 flex w-full flex-col border-l border-line bg-surface-1 shadow-overlay sm:w-[400px]"
	>
		<div class="flex min-h-header flex-shrink-0 items-center justify-between gap-3 border-b border-line px-4">
			<div class="min-w-0">
				<h2 class="truncate text-md font-semibold text-fg">{apiKey.name}</h2>
				<p class="font-mono text-sm tabular-nums text-fg-subtle">{keyFingerprint(apiKey)}</p>
			</div>
			<IconButton icon="close" label="Close" onclick={onClose} />
		</div>

		<div class="min-h-0 flex-1 space-y-5 overflow-auto p-4">
			<div class="flex items-center gap-3">
				<OwnerAvatar name={owner} size="md" />
				<div class="min-w-0">
					<p class="truncate text-sm font-medium text-fg">{owner}</p>
					<p class="text-sm text-fg-subtle">Owner</p>
				</div>
				{#if badge}<Badge variant={badge.variant} size="sm" class="ml-auto flex-shrink-0">{badge.label}</Badge>{/if}
			</div>

			<p class="text-sm text-fg-muted">{canDoText(apiKey)}</p>

			{#if adminNeverExpires(apiKey)}
				<p class="text-sm text-warning" data-testid="admin-no-expiry-note">This key can administer the instance and has no expiry. Admin keys should expire within 90 days.</p>
			{/if}

			<dl class="grid grid-cols-2 gap-3">
				<div>
					<dt class="text-sm text-fg-subtle">Created</dt>
					<dd class="font-mono text-sm tabular-nums text-fg-muted">{formatCreated(apiKey)}</dd>
				</div>
				<div>
					<dt class="text-sm text-fg-subtle">Last used</dt>
					<dd class="font-mono text-sm tabular-nums text-fg-muted">{lastUsedText(apiKey)}</dd>
				</div>
				<div>
					<dt class="text-sm text-fg-subtle">Expires</dt>
					<dd class="font-mono text-sm tabular-nums {adminNeverExpires(apiKey) ? 'text-warning' : 'text-fg-muted'}">{expiresText(apiKey)}</dd>
				</div>
			</dl>

			<div>
				<p class="label">History</p>
				{#if loading}
					<div class="flex justify-center py-4"><Spinner /></div>
				{:else if failed}
					<div class="rounded border border-line px-3 py-4 text-center">
						<p class="text-sm text-fg-muted">The history could not be loaded.</p>
						<Button size="xs" class="mt-2" onclick={load}>Retry</Button>
					</div>
				{:else}
					<ol class="divide-y divide-line rounded border border-line" data-testid="admin-key-history">
						{#each lines as line, index (index)}
							<li class="px-3 py-2">
								<div class="flex items-baseline justify-between gap-3">
									<span class="text-sm font-medium text-fg">{line.what}</span>
									<span class="font-mono text-sm tabular-nums text-fg-subtle">{line.when}</span>
								</div>
								{#if line.detail}<p class="text-sm text-fg-muted">{line.detail}</p>{/if}
							</li>
						{/each}
					</ol>
				{/if}
			</div>
		</div>

		{#if revokable(apiKey)}
			<div class="flex-shrink-0 border-t border-line px-4 py-3">
				<Button variant="ghost" size="sm" icon="ban" loading={revoking} class="!text-danger" onclick={() => onRevoke(apiKey)}>Revoke key</Button>
			</div>
		{/if}
	</div>
</div>
