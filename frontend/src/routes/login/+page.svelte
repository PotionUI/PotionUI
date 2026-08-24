<script lang="ts">
	import { authStore } from '$lib/stores/auth';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus } from '$lib/services/api/setup';
	import { shouldShowRegisterLink } from '$lib/utils/setupRouting';
	import { Alert, Button } from '$lib/components/ui';
	import AuthShell from '$lib/components/auth/AuthShell.svelte';

	let username = '';
	let password = '';
	let isLoading = false;
	let error = '';
	let rememberMe = false;
	let showPassword = false;
	let setupStatus: SetupStatus | null = null;

	$: ({ error: storeError } = $authStore);

	$: sessionExpired = $page.url.searchParams.get('expired') === '1';

	$: showRegisterLink = shouldShowRegisterLink(setupStatus);

	onMount(async () => {
		rememberMe = storage.get('remember_me') === 'true';
		try {
			setupStatus = await api.getSetupStatus();
		} catch {
			setupStatus = null;
		}
	});

	async function handleSubmit(event: Event) {
		event.preventDefault();
		error = '';

		if (!username || !password) {
			error = 'Please enter both username and password';
			return;
		}

		isLoading = true;
		storage.set('remember_me', String(rememberMe));
		const result = await authStore.login(username, password, rememberMe);
		isLoading = false;

		if (result.success) {
			goto('/generate', { replaceState: true });
		} else {
			error = result.error || 'Login failed';
		}
	}

	// Check if already logged in
	$: if ($authStore.isAuthenticated && !$authStore.loading) {
		goto('/generate');
	}
</script>

<svelte:head>
	<title>Login - PotionUI</title>
</svelte:head>

<AuthShell heading="Welcome back">
	{#snippet subtitle()}
		Sign in to <span class="text-fg-muted">Potion<span class="font-mono">UI</span></span>
	{/snippet}

	{#if sessionExpired}
		<Alert variant="warning" class="mt-6">Your session has expired. Please sign in again.</Alert>
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
			<Button
				type="submit"
				variant="primary"
				size="lg"
				class="w-full"
				loading={isLoading}
				disabled={isLoading}
			>
				{isLoading ? 'Signing in...' : 'Sign In'}
			</Button>

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

	{#if showRegisterLink}
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
