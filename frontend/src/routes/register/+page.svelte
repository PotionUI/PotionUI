<script lang="ts">
	import { onMount } from 'svelte';
	import { authStore } from '$lib/stores/auth';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus } from '$lib/services/api/setup';
	import { shouldBlockRegistration } from '$lib/utils/setupRouting';
	import { Button, Spinner } from '$lib/components/ui';
	import AuthShell from '$lib/components/auth/AuthShell.svelte';
	import AccountRegistrationForm from '$lib/components/auth/AccountRegistrationForm.svelte';
	import type { RegistrationSubmitValues } from '$lib/components/auth/AccountRegistrationForm.svelte';

	let statusChecked = false;
	let setupStatus: SetupStatus | null = null;

	onMount(async () => {
		try {
			setupStatus = await api.getSetupStatus();
		} catch {
			// Fail-soft: if the status check fails, fall back to showing the
			// form - submitting still hits the same server-side gate and will
			// surface a clear error either way.
			setupStatus = null;
		}
		statusChecked = true;
	});

	$: registrationBlocked = statusChecked && shouldBlockRegistration(setupStatus);

	async function handleRegister(values: RegistrationSubmitValues) {
		const result = await authStore.register(values.username, values.email, values.password);
		if (result.success) {
			goto('/generate');
		}
		return result;
	}

	// Check if already logged in
	$: if ($authStore.isAuthenticated && !$authStore.loading) {
		goto('/generate');
	}
</script>

<svelte:head>
	<title>Register - PotionUI</title>
</svelte:head>

{#if statusChecked && registrationBlocked}
	<AuthShell heading="This instance already has an owner">
		<p class="mt-6 text-base text-fg-muted">
			Accounts on this instance are created by its owner — ask them to add you.
		</p>
		<Button href="/login" variant="primary" size="lg" class="mt-8 w-full">Go to login</Button>
	</AuthShell>
{:else}
	<AuthShell heading="Create your account">
		{#snippet subtitle()}
			Join <span class="text-fg-muted">Potion<span class="font-mono">UI</span></span>
		{/snippet}

		{#if !statusChecked}
			<div class="mt-8">
				<Spinner />
			</div>
		{:else}
			<AccountRegistrationForm
				mode="register"
				submitLabel="Create Account"
				submitLoadingLabel="Creating account..."
				onSubmit={handleRegister}
			/>

			<p class="mt-8 text-base text-fg-subtle">
				Already have an account?
				<a
					href="/login"
					class="text-fg-muted underline decoration-line-hover underline-offset-[3px] transition-colors hover:text-fg"
				>
					Sign in here
				</a>
			</p>
		{/if}
	</AuthShell>
{/if}
