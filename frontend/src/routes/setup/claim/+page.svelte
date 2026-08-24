<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus } from '$lib/services/api/setup';
	import { Button, Spinner } from '$lib/components/ui';
	import AuthShell from '$lib/components/auth/AuthShell.svelte';
	import AccountRegistrationForm from '$lib/components/auth/AccountRegistrationForm.svelte';
	import type { RegistrationSubmitValues } from '$lib/components/auth/AccountRegistrationForm.svelte';

	let statusChecked = false;
	let status: SetupStatus | null = null;

	onMount(async () => {
		try {
			status = await api.getSetupStatus();
		} catch {
			// A failed status check shouldn't strand the operator - fall through
			// to the claim form; the register call underneath re-validates the
			// same gating server-side and will surface a clear error either way.
			status = null;
		}
		statusChecked = true;
	});

	async function handleClaim(values: RegistrationSubmitValues) {
		const result = await authStore.register(
			values.username,
			values.email,
			values.password,
			status?.claim_requires_token ? values.claimCode : undefined
		);
		if (result.success) {
			goto('/setup', { replaceState: true });
		}
		return result;
	}
</script>

<svelte:head>
	<title>Set up PotionUI</title>
</svelte:head>

{#if statusChecked && status && !status.needs_owner}
	<AuthShell heading="This instance already has an owner">
		<Button href="/login" variant="primary" size="lg" class="mt-8 w-full">Go to login</Button>
	</AuthShell>
{:else}
	<AuthShell heading="Create the owner account">
		{#snippet subtitle()}
			Set up <span class="text-fg-muted">Potion<span class="font-mono">UI</span></span>
		{/snippet}

		{#if !statusChecked}
			<div class="mt-8">
				<Spinner />
			</div>
		{:else}
			<AccountRegistrationForm
				mode="claim"
				showClaimCode={!!status?.claim_requires_token}
				claimCodeRequired={!!status?.claim_requires_token}
				submitLabel="Create owner account"
				submitLoadingLabel="Creating owner account..."
				onSubmit={handleClaim}
			/>
		{/if}
	</AuthShell>
{/if}
