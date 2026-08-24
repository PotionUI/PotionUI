<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus } from '$lib/services/api/setup';
	import { decideRootDestination, canDecideRootDestination } from '$lib/utils/setupRouting';

	let statusChecked = false;
	let setupStatus: SetupStatus | null = null;
	let stillStarting = false;

	// Self-hosted backends can take a while on a cold start (first-boot
	// migrations, model catalog build) while the frontend is already up. Failing
	// soft straight to /login on a single failed status fetch would hide a FRESH
	// instance's owner-claim screen behind a login page the user has no account
	// for. Retry for a while with an honest "starting up" message before falling
	// back.
	const STATUS_RETRY_MS = 1500;
	const STATUS_RETRY_WINDOW_MS = 60000;

	onMount(async () => {
		const deadline = Date.now() + STATUS_RETRY_WINDOW_MS;
		for (;;) {
			try {
				setupStatus = await api.getSetupStatus();
				break;
			} catch {
				if (Date.now() >= deadline) {
					// Fail-soft: a genuinely broken/older status endpoint must
					// never brick root routing - fall back to the pre-setup
					// auth-only decision.
					setupStatus = null;
					break;
				}
				stillStarting = true;
				await new Promise((resolve) => setTimeout(resolve, STATUS_RETRY_MS));
			}
		}
		stillStarting = false;
		statusChecked = true;
	});

	$: if (statusChecked && canDecideRootDestination(setupStatus, $authStore.loading)) {
		goto(decideRootDestination(setupStatus, $authStore.isAuthenticated));
	}
</script>

<div class="min-h-screen flex flex-col items-center justify-center gap-4 bg-canvas">
	<div class="spinner"></div>
	{#if stillStarting}
		<p class="text-sm text-fg-muted">Starting up — this can take a minute on the first run.</p>
	{/if}
</div>
