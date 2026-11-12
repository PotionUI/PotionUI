<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { AdminApiKey, UserApiAccess } from '$lib/services/api/apiKeys';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { parseApiError } from '$lib/utils/apiErrors';
	import { DetailSection } from '$lib/components/detail';
	import { Badge, IconButton, Spinner } from '$lib/components/ui';
	import { keyFingerprint } from '$lib/components/apikeys/apiKeyView';
	import EffectNote from './EffectNote.svelte';
	import UserApiAccessSwitches from './UserApiAccessSwitches.svelte';
	import { USER_KEYS_LIMIT, adminKeyBadge, canDoText, lastUsedText, revokeCopy, revokable } from './adminKeysView';
	import { accessNote, keyCountText } from './userAccess';

	let { userId, name }: { userId: string; name: string } = $props();

	let access = $state<UserApiAccess | null>(null);
	let keys = $state<AdminApiKey[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state(false);
	let revokingId = $state<string | null>(null);

	const note = $derived(access ? accessNote(access) : null);
	const href = $derived(`/admin?tab=settings&view=api_keys&owner=${encodeURIComponent(userId)}`);

	async function load() {
		loading = true;
		failed = false;
		try {
			const response = await api.listAdminApiKeys({ userId, status: 'all', page: 1, pageSize: USER_KEYS_LIMIT });
			if (response.success && response.data) {
				keys = response.data.items;
				total = response.data.total;
			} else failed = true;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	async function revoke(key: AdminApiKey) {
		const copy = revokeCopy(key);
		const ok = await confirmDialog({ title: copy.title, message: copy.message, variant: 'danger', confirmLabel: copy.confirmLabel });
		if (!ok) return;
		revokingId = key.id;
		try {
			const response = await api.revokeAdminApiKey(key.id);
			if (!response.success) throw new Error(response.message || 'Failed to revoke the key.');
			toasts.success('Key revoked');
			await load();
		} catch (err) {
			toasts.error(parseApiError(err).message || 'Failed to revoke the key.');
		} finally {
			revokingId = null;
		}
	}

	onMount(load);
</script>

<DetailSection label="API access">
	<div data-testid="user-api-access">
		<UserApiAccessSwitches {userId} {name} stacked onChanged={(next) => (access = next)} />
		<EffectNote note={note} testid="user-access-note" />

		<div class="mt-4 border-t border-line pt-3">
			<div class="mb-2 flex items-center justify-between gap-3">
				<p class="text-sm font-medium text-fg">Keys</p>
				<span class="font-mono text-sm tabular-nums text-fg-subtle">{keyCountText(total)}</span>
			</div>
			{#if loading && !keys.length}
				<div class="flex justify-center py-4"><Spinner /></div>
			{:else if failed}
				<button type="button" class="text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline" onclick={load}>Could not load keys. Retry</button>
			{:else if !keys.length}
				<p class="text-sm text-fg-subtle">No keys.</p>
			{:else}
				<div class="divide-y divide-line overflow-hidden rounded border border-line" data-user-keys>
					{#each keys as key (key.id)}
						{@const badge = adminKeyBadge(key)}
						<div class="flex items-center justify-between gap-3 px-3 py-2 {revokable(key) ? '' : 'opacity-50'}" data-user-key={key.id}>
							<div class="min-w-0">
								<div class="flex min-w-0 items-center gap-2">
									<p class="truncate text-sm text-fg">{key.name}</p>
									{#if badge}<Badge variant={badge.variant} size="sm" class="flex-shrink-0">{badge.label}</Badge>{/if}
								</div>
								<p class="truncate font-mono text-sm tabular-nums text-fg-subtle">{keyFingerprint(key)}</p>
								<p class="truncate text-sm text-fg-muted">{canDoText(key)} · {lastUsedText(key)}</p>
							</div>
							{#if revokable(key)}
								<IconButton icon="ban" label="Revoke key" size="sm" loading={revokingId === key.id} class="hover:!text-danger" onclick={() => revoke(key)} />
							{/if}
						</div>
					{/each}
				</div>
				{#if total > keys.length}
					<a class="mt-2 inline-block text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline" {href}>See all {total} keys</a>
				{/if}
			{/if}
		</div>
	</div>
</DetailSection>
