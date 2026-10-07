<script lang="ts">
	import { get } from 'svelte/store';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { nsfwFilterStore, type NsfwFilterMode } from '$lib/stores/nsfwFilter';
	import { autoOrganizeCounts, autoOrganizeHref } from '$lib/stores/autoOrganizeCounts';
	import { readLastSubject } from '$lib/organize/lastSubject';
	import Icon from '$lib/components/Icon.svelte';
	import { Card } from '$lib/components/ui';
	import MenuUsage from '$lib/plans/components/MenuUsage.svelte';
	import { limits, storageUsed } from '$lib/plans/store';
	import { accountsStore } from '$lib/stores/accounts';
	import { MAX_ACCOUNTS, type StoredAccount } from '$lib/stores/accountRegistry';
	import { roleLabel } from '$lib/utils/accountLabels';
	import {
		logoutAccount,
		logoutActiveAccount,
		logoutEverywhere,
		refreshAccountList,
		signInAgain,
		startAddAccount,
		switchAccount
	} from '$lib/stores/accountActions';
	import AccountAvatar from './AccountAvatar.svelte';
	import AccountRows from './AccountRows.svelte';

	type Variant = 'menu' | 'sheet';

	let { variant = 'menu', onClose }: { variant?: Variant; onClose: () => void } = $props();

	const sheet = untrack(() => variant) === 'sheet';
	const ids = sheet
		? {
				root: 'accounts-sheet',
				accounts: 'accounts-sheet-accounts',
				count: 'accounts-sheet-count',
				add: 'accounts-sheet-add',
				logout: 'accounts-sheet-logout',
				logoutAll: 'accounts-sheet-logout-all'
			}
		: {
				root: undefined,
				accounts: 'user-menu-accounts',
				count: 'user-menu-count',
				add: 'user-menu-add-account',
				logout: 'user-menu-logout',
				logoutAll: 'user-menu-logout-all'
			};

	const nsfwFilterOptions: { value: NsfwFilterMode; label: string }[] = [
		{ value: 'blur', label: 'Blur' },
		{ value: 'show', label: 'Show' },
		{ value: 'hide', label: 'Hide' }
	];

	const headerClass =
		'flex items-center justify-between px-1 pb-1.5 font-mono text-2xs uppercase tracking-wide text-fg-subtle';
	const itemClass = sheet
		? 'flex min-h-12 w-full items-center gap-3 rounded px-2.5 text-left text-base transition-colors'
		: 'flex min-h-9 w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm transition-colors';
	const itemTone = 'text-fg-muted hover:bg-surface-3 hover:text-fg';
	const iconClass = sheet ? 'h-5 w-5 shrink-0' : 'h-4 w-4 shrink-0';

	nsfwFilterStore.init();
	refreshAccountList();
	if (Object.values(get(autoOrganizeCounts)).every((c) => c === null)) void autoOrganizeCounts.load();

	let user = $derived($authStore.user);
	let accounts = $derived($accountsStore.accounts);
	let otherAccounts = $derived(accounts.filter((a) => a.userId !== user?.id));
	let accountTotal = $derived(Math.max(accounts.length, 1));
	let canAdd = $derived(accountTotal < MAX_ACCOUNTS);
	let counts = $derived(Object.values($autoOrganizeCounts).filter((c) => c !== null));
	let autoActive = $derived(counts.reduce((sum, c) => sum + c.active, 0));
	let autoAttention = $derived(counts.some((c) => c.needsAttention > 0));
	let showUsage = $derived($limits.length > 0 || $storageUsed !== null);

	function run(action: () => void) {
		onClose();
		action();
	}

	const openSettings = () => run(() => void goto('/settings'));
	const openAutoOrganize = () => run(() => void goto(autoOrganizeHref(readLastSubject())));
	const openPlan = () => run(() => void goto('/settings#plan'));
	const handleLogout = () => run(() => logoutActiveAccount());
	const handleLogoutAll = () => run(() => void logoutEverywhere());
	const handleAddAccount = () => run(() => startAddAccount());
	const handleSwitch = (account: StoredAccount) => run(() => void switchAccount(account));
	const handleSignInAgain = (account: StoredAccount) => run(() => signInAgain(account));
</script>

{#if user}
	<div
		class="flex flex-col gap-3 {sheet ? 'pb-[max(1rem,env(safe-area-inset-bottom))] pt-2' : ''}"
		role={sheet ? 'menu' : undefined}
		data-testid={ids.root}
	>
		<Card padding="none" class="!shadow-none">
			<div class="flex items-center gap-3 px-3 {sheet ? 'py-3' : 'py-2.5'}" data-testid="user-menu-identity">
				<AccountAvatar username={user.username} avatar={user.avatar_url} size="lg" />
				<div class="min-w-0 flex-1">
					<p class="truncate text-sm font-medium text-fg">{user.username}</p>
					<p class="text-2xs text-fg-subtle">{roleLabel(user.account_type)}</p>
				</div>
				<span
					class="flex shrink-0 items-center gap-1 font-mono text-2xs uppercase tracking-wide text-signal"
					data-testid="user-menu-active"
				>
					<Icon name="check" className="h-3.5 w-3.5" strokeWidth={1.5} />
					Active
				</span>
			</div>
		</Card>

		<section
			role="group"
			aria-label="Accounts"
			data-testid={otherAccounts.length > 0 || sheet ? ids.accounts : undefined}
		>
			<p class={headerClass} data-section-header>
				<span>Accounts</span>
				<span class="tabular-nums" data-testid={ids.count}>{accountTotal} / {MAX_ACCOUNTS}</span>
			</p>
			<Card padding="none" class="!shadow-none p-1">
				{#if otherAccounts.length > 0}
					<AccountRows
						accounts={otherAccounts}
						{variant}
						onSwitch={handleSwitch}
						onSignInAgain={handleSignInAgain}
						onRemove={logoutAccount}
					/>
					<div class="mx-2.5 my-1 border-t border-line"></div>
				{/if}
				<button
					type="button"
					role="menuitem"
					data-testid={ids.add}
					disabled={!canAdd}
					title={canAdd ? undefined : 'Remove an account first'}
					onclick={handleAddAccount}
					class="{itemClass} {itemTone} disabled:cursor-not-allowed disabled:opacity-50"
				>
					<span
						class="flex shrink-0 items-center justify-center rounded-full border border-dashed border-line-strong {sheet
							? 'h-9 w-9'
							: 'h-7 w-7'}"
					>
						<Icon name="plus" className="h-3.5 w-3.5" strokeWidth={1.5} />
					</span>
					<span class="flex-1">Add account</span>
					{#if !canAdd}
						<span class="shrink-0 text-2xs text-fg-subtle">Limit reached</span>
					{/if}
				</button>
			</Card>
		</section>

		<section role="group" aria-label="Workspace">
			<p class={headerClass} data-section-header><span>Workspace</span></p>
			<Card padding="none" class="!shadow-none p-1">
				<button type="button" role="menuitem" onclick={openSettings} class="{itemClass} {itemTone}">
					<Icon name="settings" className={iconClass} strokeWidth={1.5} />
					<span>Settings</span>
				</button>
				<button
					type="button"
					role="menuitem"
					data-testid="user-menu-auto-organize"
					onclick={openAutoOrganize}
					class="{itemClass} {itemTone}"
				>
					<Icon name="wand" className={iconClass} strokeWidth={1.5} />
					<span class="flex-1">Auto-organize</span>
					{#if counts.length > 0}
						{#if autoAttention}
							<span
								class="h-1.5 w-1.5 shrink-0 rounded-full bg-warning"
								role="img"
								aria-label="Needs attention"
								data-testid="user-menu-auto-organize-attention"
							></span>
						{/if}
						<span
							class="shrink-0 font-mono text-2xs tabular-nums text-fg-subtle"
							data-testid="user-menu-auto-organize-count">{autoActive}</span
						>
					{/if}
				</button>
				{#if showUsage}
					<div class="mt-1 border-t border-line pt-1" data-testid="user-menu-usage">
						<MenuUsage rows={$limits} storageBytes={$storageUsed} onOpen={openPlan} />
					</div>
				{/if}
			</Card>
		</section>

		{#if !$nsfwFilterStore.restricted}
			<section role="group" aria-label="Content">
				<p class={headerClass} data-section-header><span>Content</span></p>
				<Card padding="none" class="!shadow-none">
					<div
						class="flex gap-2 px-3 py-2 {sheet ? 'flex-col items-stretch' : 'items-center justify-between'}"
						data-testid="user-menu-content-row"
					>
						<span class="text-sm text-fg-muted">Sensitive content</span>
						<div
							class="flex rounded border border-line-strong bg-surface-3 p-0.5 {sheet ? 'w-full' : 'w-[180px]'}"
							role="radiogroup"
							aria-label="Sensitive content"
						>
							{#each nsfwFilterOptions as option (option.value)}
								<button
									type="button"
									role="radio"
									aria-checked={$nsfwFilterStore.mode === option.value}
									class="flex-1 rounded-sm px-2 text-2xs font-medium transition-colors duration-100 {sheet
										? 'min-h-10 text-xs'
										: 'py-1'} {$nsfwFilterStore.mode === option.value
										? 'bg-signal/10 text-signal'
										: 'text-fg-muted hover:text-fg'}"
									onclick={() => nsfwFilterStore.setMode(option.value)}
								>
									{option.label}
								</button>
							{/each}
						</div>
					</div>
				</Card>
			</section>
		{/if}

		<section role="group" aria-label="Session">
			<p class={headerClass} data-section-header><span>Session</span></p>
			<Card padding="none" class="!shadow-none p-1">
				<button
					type="button"
					role="menuitem"
					data-testid={ids.logout}
					onclick={handleLogout}
					class="{itemClass} text-fg-muted hover:bg-surface-3 hover:text-danger"
				>
					<Icon name="logout" className={iconClass} strokeWidth={1.5} />
					<span class="min-w-0 flex-1 truncate">Log out {user.username}</span>
				</button>
				{#if accounts.length > 1}
					<button
						type="button"
						role="menuitem"
						data-testid={ids.logoutAll}
						onclick={handleLogoutAll}
						class="{itemClass} text-fg-muted hover:bg-surface-3 hover:text-danger"
					>
						<Icon name="group" className={iconClass} strokeWidth={1.5} />
						<span class="flex-1">Log out of all accounts</span>
						<span class="shrink-0 font-mono text-2xs tabular-nums text-fg-subtle">{accounts.length}</span>
					</button>
				{/if}
			</Card>
		</section>
	</div>
{/if}
