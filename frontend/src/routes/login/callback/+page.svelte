<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { api } from '$lib/services/api/index';
	import { authStore } from '$lib/stores/auth';
	import { Alert, Spinner } from '$lib/components/ui';
	import AuthShell from '$lib/components/auth/AuthShell.svelte';

	let status: 'loading' | 'error' = 'loading';
	let errorMessage = '';
	let started = false;

	onMount(() => {
		if (started) return;
		started = true;
		exchangeCode();
	});

	async function exchangeCode() {
		const code = $page.url.searchParams.get('code');
		if (!code) {
			status = 'error';
			errorMessage = 'This sign-in link is missing its code.';
			return;
		}

		try {
			const tokenData = await api.exchangeExternalLoginCode(code);
			await authStore.adoptToken(tokenData.access_token);
			goto('/', { replaceState: true });
		} catch {
			status = 'error';
			errorMessage = 'This sign-in link is invalid or has expired.';
		}
	}
</script>

<svelte:head>
	<title>Signing in - PotionUI</title>
</svelte:head>

<AuthShell heading="Signing you in">
	{#if status === 'loading'}
		<div class="mt-10 flex flex-col items-center gap-3">
			<Spinner size="lg" />
			<p class="text-sm text-fg-muted">Completing sign-in...</p>
		</div>
	{:else}
		<Alert variant="danger" class="mt-6">{errorMessage}</Alert>
		<p class="mt-6 text-base text-fg-subtle">
			<a
				href="/login"
				class="text-fg-muted underline decoration-line-hover underline-offset-[3px] transition-colors hover:text-fg"
			>
				Back to sign in
			</a>
		</p>
	{/if}
</AuthShell>
