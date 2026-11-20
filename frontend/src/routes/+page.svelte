<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { api } from '$lib/services/api/index';
	import type { SetupStatus } from '$lib/services/api/setup';
	import { decideRootDestination, canDecideRootDestination } from '$lib/utils/setupRouting';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';

	const FAST_RETRY_MS = 1500;
	const SLOW_RETRY_MS = 5000;
	const FAST_RETRY_WINDOW_MS = 60000;
	const STARTING_AFTER_MS = 4000;

	let setupStatus = $state<SetupStatus | null>(null);
	let stage = $state<'spinner' | 'starting' | 'stalled'>('spinner');
	let errorStatus = $state<number | null>(null);
	let lastError = $state('');
	let checking = $state(false);

	let startedAt = 0;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let destroyed = false;
	let navigated = false;
	let round = 0;

	function stageForElapsed(): 'spinner' | 'starting' | 'stalled' {
		const elapsed = Date.now() - startedAt;
		if (elapsed >= FAST_RETRY_WINDOW_MS) return 'stalled';
		return elapsed >= STARTING_AFTER_MS ? 'starting' : 'spinner';
	}

	async function attempt() {
		const mine = ++round;
		checking = true;
		try {
			const status = await api.getSetupStatus();
			if (destroyed || mine !== round) return;
			checking = false;
			setupStatus = status;
			return;
		} catch (err) {
			if (destroyed || mine !== round) return;
			const status = (err as { response?: { status?: number } })?.response?.status;
			errorStatus = typeof status === 'number' ? status : null;
			lastError = err instanceof Error ? err.message : String(err);
			checking = false;
			stage = stageForElapsed();
			const delay = Date.now() - startedAt >= FAST_RETRY_WINDOW_MS ? SLOW_RETRY_MS : FAST_RETRY_MS;
			timer = setTimeout(attempt, delay);
		}
	}

	function checkNow() {
		clearTimeout(timer);
		void attempt();
	}

	onMount(() => {
		startedAt = Date.now();
		void attempt();
		const ticker = setInterval(() => {
			if (!setupStatus) stage = stageForElapsed();
		}, 1000);
		return () => {
			destroyed = true;
			clearTimeout(timer);
			clearInterval(ticker);
		};
	});

	$effect(() => {
		if (navigated || !canDecideRootDestination(setupStatus, $authStore.loading)) return;
		const destination = decideRootDestination(setupStatus, $authStore.isAuthenticated);
		if (!destination) return;
		navigated = true;
		goto(destination);
	});

	const answeredWithError = $derived(errorStatus !== null);
	const errorLine = $derived(`The server answered with an error (${errorStatus}).`);
</script>

<div class="min-h-screen flex flex-col items-center justify-center gap-4 bg-canvas px-4 py-10">
	{#if stage === 'stalled'}
		<div class="flex w-full max-w-md flex-col items-center gap-4 text-center">
			<EmptyState
				title={answeredWithError ? errorLine : "PotionUI's server is not answering yet"}
				description="Look at the window where PotionUI was started. In Docker, look at the container log."
				icon="warning"
			>
				{#snippet actions()}
					<Button variant="secondary" icon="refresh" loading={checking} onclick={checkNow}>Check now</Button>
				{/snippet}
			</EmptyState>
			<p class="text-sm text-fg-muted">Checking again every few seconds</p>
			<details class="w-full text-left">
				<summary class="cursor-pointer text-sm text-fg-muted">Details</summary>
				<p class="mt-2 break-words rounded border border-line bg-surface-1 px-3 py-2 font-mono text-xs text-fg-muted">{lastError}</p>
			</details>
		</div>
	{:else}
		<Spinner size="lg" />
		{#if stage === 'starting'}
			<p class="text-sm text-fg-muted">
				{answeredWithError ? errorLine : 'Starting up. This can take a minute on the first run.'}
			</p>
		{/if}
	{/if}
</div>
