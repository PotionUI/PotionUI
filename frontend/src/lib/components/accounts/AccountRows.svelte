<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import AccountAvatar from './AccountAvatar.svelte';
	import { roleLabel, signedInAgo } from '$lib/utils/accountLabels';
	import type { StoredAccount } from '$lib/stores/accountRegistry';

	type Variant = 'menu' | 'sheet';

	let {
		accounts,
		activeId = null,
		variant = 'menu',
		now = Date.now(),
		onSwitch,
		onSignInAgain,
		onRemove
	}: {
		accounts: StoredAccount[];
		activeId?: string | null;
		variant?: Variant;
		now?: number;
		onSwitch: (account: StoredAccount) => void;
		onSignInAgain: (account: StoredAccount) => void;
		onRemove: (account: StoredAccount) => void;
	} = $props();

	let tall = $derived(variant === 'sheet');

	function activate(account: StoredAccount) {
		if (account.expired) onSignInAgain(account);
		else if (account.userId !== activeId) onSwitch(account);
	}
</script>

{#each accounts as account (account.userId)}
	{@const isActive = account.userId === activeId}
	<div
		class="group flex items-center rounded transition-colors focus-within:bg-surface-3 hover:bg-surface-3 {isActive
			? 'bg-surface-3'
			: ''}"
		data-testid="account-row"
		data-user-id={account.userId}
	>
		<button
			type="button"
			role="menuitem"
			class="flex min-w-0 flex-1 items-center gap-2.5 rounded px-2.5 py-2 text-left {tall ? 'min-h-[52px]' : ''}"
			onclick={() => activate(account)}
		>
			<AccountAvatar
				username={account.username}
				avatar={account.avatar}
				size={tall ? 'md' : 'sm'}
				dim={account.expired}
			/>
			<span class="min-w-0 flex-1">
				<span class="block truncate text-sm text-fg {account.expired ? 'opacity-70' : ''}">{account.username}</span>
				{#if account.expired}
					<span class="block text-2xs text-warning">Session expired</span>
				{:else}
					<span class="block truncate text-2xs text-fg-subtle">
						{roleLabel(account.role)}{isActive ? '' : ` · ${signedInAgo(account.signedInAt, now)}`}
					</span>
				{/if}
			</span>
			{#if account.expired}
				<span class="shrink-0 rounded bg-surface-3 px-2 py-1 text-xs text-fg group-hover:bg-line-hover">
					Sign in again
				</span>
			{:else if isActive}
				<span class="flex shrink-0 items-center gap-1 font-mono text-2xs uppercase tracking-wide text-signal">
					<Icon name="check" className="h-3.5 w-3.5" strokeWidth={1.5} />
					Active
				</span>
			{:else}
				<span
					class="flex shrink-0 items-center gap-1 font-mono text-2xs uppercase tracking-wide text-fg-subtle group-hover:text-fg"
				>
					Switch
					<Icon name="arrow-right" className="h-3 w-3" strokeWidth={1.5} />
				</span>
			{/if}
		</button>
		{#if !isActive}
			<IconButton
				icon="close"
				label="Log out {account.username}"
				size="sm"
				class="mr-1 shrink-0 {tall
					? ''
					: 'opacity-0 focus:opacity-100 group-focus-within:opacity-100 group-hover:opacity-100'}"
				onclick={() => onRemove(account)}
			/>
		{/if}
	</div>
{/each}
