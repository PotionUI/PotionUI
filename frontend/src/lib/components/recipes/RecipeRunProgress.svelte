<script lang="ts">
	import type { Snippet } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { SetupConsentProvider, SetupRun } from '$lib/services/api/setup';
	import type { RecipeRunActions } from './runActions';
	import {
		runBadgeVariant,
		runStatusLabel,
		manifestStepBadgeVariant,
		manifestStepStatusLabel,
		resolveStepGroups,
		runManifestProgressSummary,
		stepDuration,
		stepProgressLabel,
		stepProgressPercent,
		isConsentStatus,
		runNeedsConsent,
		canRetryRun,
		extractConsentRequest,
		computeTransferStats,
		type TransferStats
	} from '$lib/utils/setupRunDisplay';
	import { formatBytes, formatDuration } from '$lib/utils/format';
	import { Alert, Badge, Button, Input } from '$lib/components/ui';

	/**
	 * The live view of one recipe run: progress summary, consent gate, failure
	 * with retry, and the ordered step rows. Shared by the /setup wizard and
	 * Admin -> Recipes so both surfaces render a run identically.
	 *
	 * Polling belongs to the caller — this component never fetches the run, it
	 * only hands back whatever a user action returned via `onRunUpdated`, so
	 * the caller can reschedule its own poll. The two mutating calls come in as
	 * `actions` because the wizard drives the setup endpoints and the admin tab
	 * drives the recipes ones; the provider-credential call is the same plugin
	 * settings endpoint on both surfaces, so it stays here.
	 */
	let {
		run,
		title = 'Guided setup',
		fetchError = '',
		actions,
		onRunUpdated,
		onStartOver,
		completed
	}: {
		run: SetupRun;
		title?: string;
		/** Caller-owned "couldn't reach the server" notice, shown under the header. */
		fetchError?: string;
		actions: RecipeRunActions;
		onRunUpdated: (run: SetupRun) => void;
		/** Escape hatch out of a failed run; the link is omitted when absent. */
		onStartOver?: () => void;
		/** Rendered between the header and the consent gate — the wizard's
		 * first-generation handoff, which no other surface has. */
		completed?: Snippet;
	} = $props();

	const resolvedSteps = $derived(resolveStepGroups(run));
	const consentGroup = $derived(
		runNeedsConsent(run)
			? (resolvedSteps.groups.find((g) => isConsentStatus(g.status as any)) ?? null)
			: null
	);
	const consentRequest = $derived(consentGroup ? extractConsentRequest(consentGroup.latest) : null);

	// The server reports plain byte counts per poll; speed/ETA are reconstructed
	// here from two consecutive polls, one sample per step —
	// `lastProgressSamples` is a plain mutated Map (not reactive state)
	// precisely so recording into it never re-triggers this block.
	const lastProgressSamples = new Map<string, { bytes: number; at: number }>();
	let transferStatsByStep = $state<Record<string, TransferStats>>({});

	$effect(() => {
		const next: Record<string, TransferStats> = {};
		for (const group of resolvedSteps.groups) {
			const current = group.latest?.progress_current;
			if (current == null) continue;
			const sample = { bytes: current, at: Date.now() };
			next[group.stepKey] = computeTransferStats(
				lastProgressSamples.get(group.stepKey) ?? null,
				sample,
				group.latest?.progress_total ?? null
			);
			lastProgressSamples.set(group.stepKey, sample);
		}
		transferStatsByStep = next;
	});

	let retryBusy = $state(false);
	let retryError = $state('');
	let consentBusy = $state(false);
	let consentError = $state('');
	let cancelBusy = $state(false);
	let cancelError = $state('');

	// Optional inline "add a provider API key" field the consent gate offers
	// when `consentRequest.providers` names one that isn't configured yet —
	// keyed by provider id so more than one can be prompted for at once.
	let credentialDrafts = $state<Record<string, string>>({});
	let credentialBusy = $state<Record<string, boolean>>({});
	let credentialError = $state<Record<string, string>>({});
	let credentialSaved = $state<Record<string, boolean>>({});

	function errorText(err: any, fallback: string): string {
		return err?.response?.data?.detail || err?.message || fallback;
	}

	async function saveProviderCredential(provider: SetupConsentProvider) {
		const value = (credentialDrafts[provider.id] || '').trim();
		if (!value || credentialBusy[provider.id]) return;
		credentialBusy = { ...credentialBusy, [provider.id]: true };
		credentialError = { ...credentialError, [provider.id]: '' };
		try {
			await api.saveSetupProviderCredential(provider.id, provider.field_name, value);
			credentialSaved = { ...credentialSaved, [provider.id]: true };
		} catch (err: any) {
			credentialError = {
				...credentialError,
				[provider.id]: errorText(err, "Couldn't save the API key.")
			};
		} finally {
			credentialBusy = { ...credentialBusy, [provider.id]: false };
		}
	}

	async function approveConsent() {
		if (!consentGroup || consentBusy) return;
		consentBusy = true;
		consentError = '';
		try {
			onRunUpdated(await actions.grantConsent(run.id, consentGroup.stepKey));
		} catch (err: any) {
			consentError = errorText(err, "Couldn't approve the download.");
		} finally {
			consentBusy = false;
		}
	}

	async function cancelRun() {
		if (cancelBusy) return;
		cancelBusy = true;
		cancelError = '';
		try {
			onRunUpdated(await actions.applyAction(run.id, 'cancel'));
		} catch (err: any) {
			cancelError = errorText(err, "Couldn't cancel setup.");
		} finally {
			cancelBusy = false;
		}
	}

	async function retryRun() {
		if (retryBusy) return;
		const runId = run.id;
		const previous = run;
		// Optimistic flip so the failed state doesn't linger while the request
		// is in flight.
		onRunUpdated({ ...previous, status: 'running', error_code: null, safe_error_detail: null });
		retryBusy = true;
		retryError = '';
		try {
			onRunUpdated(await actions.applyAction(runId, 'retry_step'));
		} catch (err: any) {
			onRunUpdated(previous);
			retryError = errorText(err, "Couldn't start the retry.");
		} finally {
			retryBusy = false;
		}
	}
</script>

<div class="space-y-4">
	<div class="flex items-start justify-between gap-3">
		<div class="min-w-0">
			<h2 class="text-sm font-semibold text-fg">{title}</h2>
			<p class="text-sm text-fg-muted mt-0.5">{runManifestProgressSummary(resolvedSteps)}</p>
		</div>
		<Badge variant={runBadgeVariant(run.status)}>{runStatusLabel(run.status)}</Badge>
	</div>

	{#if fetchError}
		<p class="text-xs text-fg-subtle">{fetchError}</p>
	{/if}

	{@render completed?.()}

	{#if consentGroup && consentRequest}
		<div class="rounded border border-signal/30 bg-signal/5 px-3 py-3 space-y-3">
			<div>
				<p class="text-sm font-semibold text-signal">Needs your go-ahead</p>
				<p class="text-sm text-fg-muted mt-0.5">
					{consentGroup.title} wants to download the following before it can continue:
				</p>
			</div>

			<ul class="space-y-1">
				{#each consentRequest.artifacts as artifact (artifact.id)}
					<li class="flex items-center justify-between gap-3 text-sm">
						<span class="text-fg truncate">{artifact.display_name}</span>
						{#if artifact.size_bytes != null}
							<span class="font-mono tabular-nums text-fg-subtle shrink-0">
								{formatBytes(artifact.size_bytes)}
							</span>
						{/if}
					</li>
				{/each}
			</ul>

			{#if consentRequest.total_bytes != null}
				<div class="flex items-center justify-between text-sm border-t border-signal/20 pt-2">
					<span class="text-fg-muted">Total</span>
					<span class="font-mono tabular-nums text-fg">{formatBytes(consentRequest.total_bytes)}</span>
				</div>
			{/if}

			{#each consentRequest.providers ?? [] as provider (provider.id)}
				{#if provider.configured || credentialSaved[provider.id]}
					<p class="text-xs text-success">{provider.name} API key saved.</p>
				{:else}
					<div class="rounded border border-line bg-surface-1 px-3 py-2 space-y-2">
						<p class="text-xs text-fg-muted">
							{provider.name} needs a free API key for some downloads —
							{#if provider.website}
								<a
									href={provider.website}
									target="_blank"
									rel="noreferrer"
									class="text-signal hover:underline"
								>
									get one
								</a>,
							{/if}
							paste it here, or continue without.
						</p>
						<div class="flex items-center gap-2">
							<Input
								type="password"
								autocomplete="off"
								class="flex-1 text-sm"
								placeholder="{provider.name} API key"
								bind:value={credentialDrafts[provider.id]}
							/>
							<Button
								size="sm"
								variant="secondary"
								loading={credentialBusy[provider.id]}
								disabled={!credentialDrafts[provider.id]?.trim()}
								onclick={() => saveProviderCredential(provider)}
							>
								Save
							</Button>
						</div>
						{#if credentialError[provider.id]}
							<p class="text-xs text-danger">{credentialError[provider.id]}</p>
						{/if}
					</div>
				{/if}
			{/each}

			<div class="flex items-center justify-between gap-3 pt-1">
				<Button size="sm" variant="primary" loading={consentBusy} onclick={approveConsent}>
					Approve and download
				</Button>
				<button
					type="button"
					class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted disabled:opacity-50"
					disabled={cancelBusy}
					onclick={cancelRun}
				>
					Cancel setup instead
				</button>
			</div>
			{#if consentError}
				<p class="text-xs text-danger">{consentError}</p>
			{/if}
			{#if cancelError}
				<p class="text-xs text-danger">{cancelError}</p>
			{/if}
		</div>
	{/if}

	{#if run.status === 'failed'}
		{@const failedRunStatus = run.status}
		<Alert variant="danger" density="compact" title="Setup couldn't finish">
			{#if run.safe_error_detail}
				<p>{run.safe_error_detail}</p>
			{/if}
			{#if retryError}
				<p class="text-xs mt-1">{retryError}</p>
			{/if}
			{#if onStartOver}
				<div class="mt-2">
					<button
						type="button"
						class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted"
						onclick={onStartOver}
					>
						Start over with a different recipe instead
					</button>
				</div>
			{/if}
			{#snippet actions()}
				{#if canRetryRun(failedRunStatus)}
					<Button size="sm" variant="secondary" loading={retryBusy} onclick={retryRun}>
						Try again
					</Button>
				{/if}
			{/snippet}
		</Alert>
	{/if}

	{#if resolvedSteps.groups.length > 0}
		<div class="space-y-2">
			{#each resolvedSteps.groups as group (group.stepKey)}
				{@const isConsent = isConsentStatus(group.status as any)}
				{@const isPending = group.status === 'pending'}
				{@const duration = group.latest ? stepDuration(group.latest) : null}
				{@const progress = group.status === 'running' ? stepProgressLabel(group.latest) : null}
				{@const percent = group.status === 'running' ? stepProgressPercent(group.latest) : null}
				{@const transfer = transferStatsByStep[group.stepKey]}
				<div
					class="rounded border px-3 py-2 {isConsent
						? 'border-signal/30 bg-signal/5'
						: isPending
							? 'border-line bg-surface-1'
							: 'border-line bg-surface-2'}"
				>
					<div class="flex items-center justify-between gap-3">
						<div class="flex items-center gap-2 min-w-0">
							<p class="text-sm font-medium {isPending ? 'text-fg-subtle' : 'text-fg'} truncate">
								{group.title}
							</p>
							<Badge size="sm" variant={manifestStepBadgeVariant(group.status)}>
								{manifestStepStatusLabel(group.status)}
							</Badge>
						</div>
						{#if duration}
							<span class="text-2xs font-mono tabular-nums text-fg-subtle shrink-0">
								{duration}
							</span>
						{/if}
					</div>

					{#if isConsent}
						<p class="text-sm text-fg-muted mt-1">
							This step needs your go-ahead before it can continue.
						</p>
					{/if}

					{#if group.status === 'running' && percent != null}
						<div class="mt-2 h-1.5 bg-surface-3 rounded-sm overflow-hidden">
							<div
								class="h-full bg-signal-solid transition-all duration-300"
								style="width: {percent}%"
							></div>
						</div>
					{/if}

					{#if progress || percent != null}
						<div
							class="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs font-mono tabular-nums text-fg-subtle mt-1"
						>
							{#if percent != null}<span>{percent}%</span>{/if}
							{#if progress}<span>{progress}</span>{/if}
							{#if transfer?.bytesPerSecond}<span>{formatBytes(transfer.bytesPerSecond)}/s</span>{/if}
							{#if transfer?.etaMs != null}<span>ETA {formatDuration(transfer.etaMs)}</span>{/if}
						</div>
						{#if group.kind === 'artifacts.fetch'}
							<a
								href="/admin?tab=downloads"
								class="text-2xs text-fg-subtle hover:text-fg-muted underline decoration-dotted mt-1 inline-block"
							>
								View in Downloads
							</a>
						{/if}
					{/if}

					{#if group.latest?.status === 'failed' && group.latest.safe_error_detail}
						<details class="mt-1">
							<summary class="text-sm text-danger cursor-pointer">Why it didn't finish</summary>
							<p class="text-sm text-fg-muted mt-1">
								{group.latest.safe_error_detail}
							</p>
							{#if group.latest.safe_suggested_action}
								<p class="text-sm text-fg-muted mt-1">{group.latest.safe_suggested_action}</p>
							{/if}
						</details>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
