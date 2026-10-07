<script lang="ts">
	import { authStore } from '$lib/stores/auth';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus, LoginProvider } from '$lib/services/api/index';
	import { shouldShowRegisterLink } from '$lib/utils/setupRouting';
	import { normalizeLoginProviders } from './loginProviders';
	import { Alert, Button } from '$lib/components/ui';
	import AuthShell from '$lib/components/auth/AuthShell.svelte';
	import ContinueAsList from '$lib/components/accounts/ContinueAsList.svelte';
	import {
		ADDING_ACCOUNT_FLAG,
		accountsStore,
		addAccountWithPassword,
		stayOnCurrentAccount,
		switchTo,
		type AddOutcome
	} from '$lib/stores/accounts';
	import { activeAccount, type StoredAccount } from '$lib/stores/accountRegistry';
	import { ADD_ACCOUNT_BLOCKED_MESSAGE, switchAccount } from '$lib/stores/accountActions';
	import { toasts } from '$lib/stores/toast';

	let username = '';
	let password = '';
	let isLoading = false;
	let error = '';
	let rememberMe = false;
	let showPassword = false;
	let setupStatus: SetupStatus | null = null;
	let loginProviders: LoginProvider[] = [];

	$: ({ error: storeError } = $authStore);

	let duplicate: { username: string; userId: string } | null = null;
	let busyId: string | null = null;

	$: sessionExpired = $page.url.searchParams.get('expired') === '1';
	$: addMode = $page.url.searchParams.get('add') === '1' && $authStore.isAuthenticated;
	$: dupParam = $page.url.searchParams.get('dup');
	$: activeEntry = activeAccount($accountsStore);
	$: continueAccounts = addMode ? [] : $accountsStore.accounts.filter((a) => !a.expired);
	$: expiredActive = !addMode && !!activeEntry?.expired;
	$: if (dupParam && !duplicate) {
		const known = $accountsStore.accounts.find((a) => a.username === dupParam);
		if (known) duplicate = { username: known.username, userId: known.userId };
	}
	$: externalLoginFailed = $page.url.searchParams.get('error') === 'external_login';

	$: showRegisterLink = shouldShowRegisterLink(setupStatus);

	onMount(async () => {
		rememberMe = storage.get('remember_me') === 'true';
		const requested = $page.url.searchParams.get('as');
		if (requested) username = requested;
		else if (!addMode && activeEntry?.expired) username = activeEntry.username;
		try {
			setupStatus = await api.getSetupStatus();
		} catch {
			setupStatus = null;
		}
		try {
			loginProviders = normalizeLoginProviders(await api.getLoginProviders());
		} catch {
			loginProviders = [];
		}
	});

	function continueWithProvider(provider: LoginProvider) {
		let path = provider.start_path;
		if (addMode) {
			try {
				sessionStorage.setItem(ADDING_ACCOUNT_FLAG, '1');
			} catch {
				error = 'This browser blocks the storage needed to add an account.';
				return;
			}
			path += `${path.includes('?') ? '&' : '?'}prompt=select_account`;
		}
		window.location.href = path;
	}

	async function continueAs(account: StoredAccount) {
		error = '';
		busyId = account.userId;
		const outcome = await switchTo(account.userId);
		if (!outcome.ok) {
			busyId = null;
			error =
				outcome.reason === 'expired'
					? `${account.username}'s session has expired. Sign in again below.`
					: `Could not switch to ${account.username}. Try again.`;
			if (outcome.reason === 'expired') username = account.username;
		}
	}

	function handleAddOutcome(outcome: AddOutcome) {
		if (outcome.status === 'duplicate') {
			duplicate = { username: outcome.username, userId: outcome.userId };
		} else if (outcome.status === 'refreshed') {
			toasts.success(`${outcome.username} signed in again`);
			void goto('/generate', { replaceState: true });
		} else if (outcome.status === 'full') {
			error = ADD_ACCOUNT_BLOCKED_MESSAGE;
		} else if (outcome.status === 'error') {
			error = outcome.message;
		}
	}

	async function stayAsCurrent() {
		await stayOnCurrentAccount();
		void goto('/generate', { replaceState: true });
	}

	async function handleSubmit(event: Event) {
		event.preventDefault();
		error = '';

		if (!username || !password) {
			error = 'Please enter both username and password';
			return;
		}

		isLoading = true;
		storage.set('remember_me', String(rememberMe));
		if (addMode) {
			const outcome = await addAccountWithPassword(username, password, rememberMe);
			isLoading = false;
			handleAddOutcome(outcome);
			return;
		}
		const result = await authStore.login(username, password, rememberMe);
		isLoading = false;

		if (result.success) {
			authStore.finishSignIn('/generate');
		} else {
			error = result.error || 'Login failed';
		}
	}

	$: if ($authStore.isAuthenticated && !$authStore.loading && !addMode && $page.url.searchParams.get('add') !== '1') {
		goto('/generate');
	}
</script>

<svelte:head>
	<title>Login - PotionUI</title>
</svelte:head>

<AuthShell heading={addMode ? 'Add an account' : 'Welcome back'}>
	{#snippet subtitle()}
		Sign in to <span class="text-fg-muted">Potion<span class="font-mono">UI</span></span>
		{#if addMode}as someone else{/if}
	{/snippet}

	{#if addMode && !duplicate}
		<Alert variant="info" class="mt-6">
			You stay signed in as <span class="text-fg">{activeEntry?.username ?? $authStore.user?.username}</span>. This
			adds a second account you can switch to from the account menu.
		</Alert>
	{/if}

	{#if duplicate}
		<Alert variant="warning" class="mt-6">
			<span class="text-fg">{duplicate.username} is already signed in.</span> Nothing was added. This browser already
			holds a session for {duplicate.username}, so it was refreshed instead.
		</Alert>
	{:else if sessionExpired || expiredActive}
		<Alert variant="warning" class="mt-6">
			{#if continueAccounts.length > 0 && activeEntry?.expired}
				{activeEntry.username}'s session has expired. Pick another account, or sign in again below.
			{:else}
				Your session has expired. Please sign in again.
			{/if}
		</Alert>
	{/if}

	{#if continueAccounts.length > 0}
		<ContinueAsList accounts={continueAccounts} {busyId} onContinue={continueAs} />
		<div
			class="mt-6 flex items-center gap-3 font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle"
		>
			<span class="h-px flex-1 bg-line"></span>
			<span>or sign in</span>
			<span class="h-px flex-1 bg-line"></span>
		</div>
	{/if}

	{#if externalLoginFailed}
		<Alert variant="danger" class="mt-6">
			Single sign-on did not complete. Your account may not be allowed to sign in here yet. Ask an administrator.
		</Alert>
	{/if}

	{#if error || storeError}
		<Alert variant="danger" class="mt-6">{error || storeError}</Alert>
	{/if}

	<form on:submit={handleSubmit}>
		<div class="mt-[38px] flex flex-col gap-[22px]">
			<div class="flex flex-col">
				<label for="username" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
					Username
				</label>
				<input
					id="username"
					type="text"
					bind:value={username}
					disabled={isLoading}
					autocomplete="username"
					class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
				/>
			</div>

			<div class="flex flex-col">
				<div class="flex items-center justify-between">
					<label
						for="password"
						class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle"
					>
						Password
					</label>
					<button
						type="button"
						class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle transition-colors hover:text-fg-muted"
						on:click={() => (showPassword = !showPassword)}
						aria-label={showPassword ? 'Hide password' : 'Show password'}
					>
						{showPassword ? 'Hide' : 'Show'}
					</button>
				</div>
				<input
					id="password"
					type={showPassword ? 'text' : 'password'}
					bind:value={password}
					disabled={isLoading}
					autocomplete="current-password"
					class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
				/>
			</div>
		</div>

		<div class="mt-[34px] flex flex-col gap-5">
			{#if duplicate}
				<div class="flex gap-3">
					<Button
						variant="primary"
						size="lg"
						class="flex-1"
						onclick={() => {
							const known = $accountsStore.accounts.find((a) => a.userId === duplicate?.userId);
							if (known) void switchAccount(known);
						}}
					>
						Switch to {duplicate.username}
					</Button>
					<Button variant="secondary" size="lg" class="flex-1" onclick={stayAsCurrent}>
						Stay as {activeEntry?.username ?? $authStore.user?.username}
					</Button>
				</div>
			{:else}
				<Button
					type="submit"
					variant="primary"
					size="lg"
					class="w-full"
					loading={isLoading}
					disabled={isLoading}
				>
					{#if addMode}
						{isLoading ? 'Adding...' : 'Add account'}
					{:else}
						{isLoading ? 'Signing in...' : 'Sign In'}
					{/if}
				</Button>
			{/if}

			<div class="flex items-center gap-2 text-base text-fg-subtle">
				<span class="relative inline-flex h-[15px] w-[15px] shrink-0">
					<input
						id="remember-me"
						type="checkbox"
						bind:checked={rememberMe}
						disabled={isLoading}
						class="peer sr-only"
					/>
					<span
						class="pointer-events-none absolute inset-0 rounded border border-line-hover peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-canvas"
					></span>
					<svg
						class="pointer-events-none absolute inset-0 h-full w-full text-fg opacity-0 transition-opacity peer-checked:opacity-100"
						viewBox="0 0 15 15"
						fill="none"
						aria-hidden="true"
					>
						<path
							d="M3 7.5L6.5 11L12 4.5"
							stroke="currentColor"
							stroke-width="1.5"
							stroke-linecap="round"
							stroke-linejoin="round"
						/>
					</svg>
				</span>
				<label for="remember-me" class="cursor-pointer select-none">Stay signed in</label>
			</div>
		</div>
	</form>

	{#if loginProviders.length > 0}
		<div
			class="mt-6 flex items-center gap-3 font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle"
		>
			<span class="h-px flex-1 bg-line"></span>
			<span>or</span>
			<span class="h-px flex-1 bg-line"></span>
		</div>

		<div class="mt-6 flex flex-col gap-3">
			{#each loginProviders as provider (provider.id)}
				<Button
					variant="primary"
					size="lg"
					class="w-full"
					onclick={() => continueWithProvider(provider)}
				>
					Continue with {provider.label}
				</Button>
			{/each}
		</div>
	{/if}

	{#if addMode}
		<p class="mt-8 text-base text-fg-subtle">
			<a
				href="/generate"
				class="text-fg-muted underline decoration-line-hover underline-offset-[3px] transition-colors hover:text-fg"
			>
				Cancel, back to {activeEntry?.username ?? $authStore.user?.username}
			</a>
		</p>
	{/if}

	{#if showRegisterLink && !addMode}
		<p class="mt-8 text-base text-fg-subtle">
			Don't have an account?
			<a
				href="/register"
				class="text-fg-muted underline decoration-line-hover underline-offset-[3px] transition-colors hover:text-fg"
			>
				Register here
			</a>
		</p>
	{/if}
</AuthShell>
