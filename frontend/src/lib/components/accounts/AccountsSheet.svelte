<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import StudioSheet from '../../../routes/generate/components/studio/StudioSheet.svelte';
	import AccountRows from './AccountRows.svelte';
	import { accountsStore } from '$lib/stores/accounts';
	import { MAX_ACCOUNTS, type StoredAccount } from '$lib/stores/accountRegistry';
	import {
		logoutAccount,
		logoutActiveAccount,
		logoutEverywhere,
		refreshAccountList,
		signInAgain,
		startAddAccount,
		switchAccount
	} from '$lib/stores/accountActions';

	export let onClose: () => void;
	export let activeUsername = '';

	refreshAccountList();

	$: accounts = $accountsStore.accounts;
	$: activeId = $accountsStore.activeId;
	$: ordered = [
		...accounts.filter((a) => a.userId === activeId),
		...accounts.filter((a) => a.userId !== activeId)
	];
	$: canAdd = accounts.length < MAX_ACCOUNTS;
	$: activeName = accounts.find((a) => a.userId === activeId)?.username ?? activeUsername;

	const itemClass =
		'flex min-h-12 w-full items-center gap-3 rounded px-2.5 text-left text-base text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg';

	function run(action: () => void) {
		onClose();
		action();
	}
</script>

<StudioSheet ariaLabel="Accounts" maxHeight="85%" on:close={onClose}>
	<div slot="header" class="flex items-center justify-between pb-2 pt-1 font-mono text-2xs uppercase tracking-wide text-fg-subtle">
		<span>Accounts</span>
		<span class="tabular-nums" data-testid="accounts-sheet-count">{accounts.length} / {MAX_ACCOUNTS}</span>
	</div>
	<div class="flex flex-col gap-0.5 pb-[max(1rem,env(safe-area-inset-bottom))]" role="menu" data-testid="accounts-sheet">
		<AccountRows
			accounts={ordered}
			{activeId}
			variant="sheet"
			onSwitch={(account: StoredAccount) => run(() => void switchAccount(account))}
			onSignInAgain={(account: StoredAccount) => run(() => signInAgain(account))}
			onRemove={(account: StoredAccount) => logoutAccount(account)}
		/>
		<button
			type="button"
			role="menuitem"
			class="{itemClass} disabled:cursor-not-allowed disabled:opacity-50"
			disabled={!canAdd}
			data-testid="accounts-sheet-add"
			on:click={() => run(() => startAddAccount())}
		>
			<Icon name="plus" className="h-5 w-5" strokeWidth={1.5} />
			<span>Add account</span>
		</button>
		<div class="my-1 border-t border-line"></div>
		<button
			type="button"
			role="menuitem"
			class={itemClass}
			data-testid="accounts-sheet-logout"
			on:click={() => run(() => logoutActiveAccount())}
		>
			<Icon name="logout" className="h-5 w-5" strokeWidth={1.5} />
			<span>Log out {activeName}</span>
		</button>
		{#if accounts.length > 1}
			<button
				type="button"
				role="menuitem"
				class={itemClass}
				data-testid="accounts-sheet-logout-all"
				on:click={() => run(() => void logoutEverywhere())}
			>
				<Icon name="group" className="h-5 w-5" strokeWidth={1.5} />
				<span>Log out of all accounts</span>
			</button>
		{/if}
	</div>
</StudioSheet>
