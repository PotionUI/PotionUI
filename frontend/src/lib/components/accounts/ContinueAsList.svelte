<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import AccountAvatar from './AccountAvatar.svelte';
	import { roleLabel } from '$lib/utils/accountLabels';
	import type { StoredAccount } from '$lib/stores/accountRegistry';

	let {
		accounts,
		busyId = null,
		onContinue
	}: {
		accounts: StoredAccount[];
		busyId?: string | null;
		onContinue: (account: StoredAccount) => void;
	} = $props();
</script>

{#if accounts.length > 0}
	<section class="mt-6" aria-label="Continue as" data-testid="continue-as">
		<h2 class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">Continue as</h2>
		<div class="mt-2 flex flex-col gap-1 rounded-lg border border-line bg-surface-1 p-1">
			{#each accounts as account (account.userId)}
				<button
					type="button"
					class="group flex w-full items-center gap-3 rounded px-3 py-2 text-left transition-colors hover:bg-surface-3 focus-visible:bg-surface-3 disabled:opacity-60"
					data-testid="continue-as-row"
					data-user-id={account.userId}
					disabled={busyId !== null}
					onclick={() => onContinue(account)}
				>
					<AccountAvatar username={account.username} avatar={account.avatar} size="sm" />
					<span class="min-w-0 flex-1">
						<span class="block truncate text-sm text-fg">{account.username}</span>
						<span class="block text-2xs text-fg-subtle">{roleLabel(account.role)}</span>
					</span>
					<span
						class="flex shrink-0 items-center gap-1 font-mono text-2xs uppercase tracking-wide text-fg-subtle group-hover:text-fg"
					>
						{busyId === account.userId ? 'Switching' : 'Continue'}
						<Icon name="arrow-right" className="h-3 w-3" strokeWidth={1.5} />
					</span>
				</button>
			{/each}
		</div>
	</section>
{/if}
