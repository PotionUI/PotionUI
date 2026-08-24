<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { authStore } from '$lib/stores/auth';
	import { tabsStore } from '$lib/stores/tabs';
	import { api } from '$lib/services/api/index';
	import type { ReadinessReport, SetupRecipe, SetupRun, SetupConsentProvider } from '$lib/services/api/setup';
	import {
		readinessBadgeVariant,
		readinessAreaLabel,
		readinessAdminLink,
		readinessHeadline
	} from '$lib/utils/readinessDisplay';
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
		shouldPollRun,
		decideRunDiscovery,
		extractConsentRequest,
		extractGenerationHandoff,
		extractSmokeGeneration,
		computeTransferStats,
		RUN_POLL_INTERVAL_MS,
		type SetupManifestStepGroup,
		type TransferStats
	} from '$lib/utils/setupRunDisplay';
	import { formatBytes, formatDuration } from '$lib/utils/format';
	import { notifySetupCompleted } from '$lib/stores/setupCompletion';
	import { Badge, Button, Card, EmptyState, Input, PageContainer, PageHeader, Spinner, Alert } from '$lib/components/ui';

	$: isAdmin = $authStore.user?.account_type === 'ADMIN';

	let report: ReadinessReport | null = null;
	let loading = true;
	let refreshing = false;
	let loadError = '';

	async function load(isRefresh = false) {
		if (isRefresh) refreshing = true;
		loadError = '';
		try {
			report = await api.getReadiness();
		} catch (err: any) {
			loadError = err?.response?.data?.detail?.message || err?.message || 'Could not load readiness.';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	// --- guided setup run --------------------------------------------------
	//
	// The server is the source of truth for "is there an active run" — this
	// page always calls `GET /api/setup/runs/active` on load, so a run
	// started in another browser/session still shows up here. The last-seen
	// run id is still kept in localStorage, but only as an optimistic
	// fast-path (paint whatever was last seen immediately, while the real
	// check is in flight) — a 404 from the server always wins and clears it.
	const RUN_STORAGE_KEY = 'potionui:setup:activeRunId';

	let run: SetupRun | null = null;
	let runChecked = false;
	let runFetchError = '';
	let retryBusy = false;
	let retryError = '';
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	$: resolvedSteps = run
		? resolveStepGroups(run)
		: ({ groups: [] as SetupManifestStepGroup[], hasManifest: false });
	$: consentGroup =
		run && runNeedsConsent(run)
			? (resolvedSteps.groups.find((g) => isConsentStatus(g.status as any)) ?? null)
			: null;
	$: consentRequest = consentGroup ? extractConsentRequest(consentGroup.latest) : null;
	// Pokes the sidebar's "Resume setup" nudge to re-check the instant this
	// run finishes, instead of it lingering until the next full page load —
	// once per page lifetime is enough (the sidebar itself de-dupes further).
	let notifiedCompletion = false;
	$: if (run?.status === 'completed' && !notifiedCompletion) {
		notifiedCompletion = true;
		notifySetupCompleted();
	}
	$: generationHandoff = run ? extractGenerationHandoff(run) : null;
	$: smokeResult = run ? extractSmokeGeneration(run) : null;
	$: smokeThumbnailUrl =
		smokeResult?.filename && smokeResult.generationId
			? api.getGenerationThumbnailURL(smokeResult.generationId, smokeResult.filename, 'medium')
			: null;

	// --- download transfer stats (speed/ETA) --------------------------------
	//
	// The server reports plain byte counts per poll (see `stepProgressLabel`);
	// speed/ETA are reconstructed here from two consecutive polls, one sample
	// per step — `lastProgressSamples` is a plain mutated Map (not reactive
	// state) precisely so recording into it never re-triggers this block.
	const lastProgressSamples = new Map<string, { bytes: number; at: number }>();
	let transferStatsByStep: Record<string, TransferStats> = {};

	$: if (run) {
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
	}

	function storedRunId(): string | null {
		if (!browser) return null;
		try {
			return localStorage.getItem(RUN_STORAGE_KEY);
		} catch {
			return null;
		}
	}

	function rememberRunId(runId: string) {
		if (!browser) return;
		try {
			localStorage.setItem(RUN_STORAGE_KEY, runId);
		} catch {
			// localStorage may be unavailable — the panel still works for this
			// page load, it just won't survive a refresh.
		}
	}

	function forgetRunId() {
		if (!browser) return;
		try {
			localStorage.removeItem(RUN_STORAGE_KEY);
		} catch {
			// ignore
		}
	}

	function clearPoll() {
		if (pollTimer) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	}

	function schedulePoll(runId: string) {
		clearPoll();
		pollTimer = setTimeout(() => refreshRun(runId), RUN_POLL_INTERVAL_MS);
	}

	async function refreshRun(runId: string) {
		try {
			const fetched = await api.getSetupRun(runId);
			run = fetched;
			runFetchError = '';
			rememberRunId(fetched.id);
			if (shouldPollRun(fetched.status)) schedulePoll(fetched.id);
		} catch (err: any) {
			// Fail soft: a stale/deleted run id means give up quietly; any other
			// fetch error (network blip, timeout) keeps the last-known view on
			// screen with a small retry notice, and keeps trying.
			if (err?.response?.status === 404) {
				forgetRunId();
				run = null;
				return;
			}
			runFetchError = "Couldn't check on setup progress — trying again shortly.";
			schedulePoll(runId);
		}
	}

	async function discoverRun() {
		const cachedId = storedRunId();
		let cachedRun: SetupRun | null = null;
		if (cachedId) {
			// Optimistic fast path: paint whatever this browser last saw for
			// this run id immediately, while the authoritative check below is
			// still in flight. Best-effort only — any failure here is ignored,
			// the authoritative call settles the real state.
			try {
				cachedRun = await api.getSetupRun(cachedId);
				run = cachedRun;
			} catch {
				// ignore — a 404/network error here just means there's nothing to
				// fall back on below.
			}
		}

		try {
			const active = await api.getSetupActiveRun();
			run = active;
			runFetchError = '';
			rememberRunId(active.id);
			if (shouldPollRun(active.status)) schedulePoll(active.id);
		} catch (err: any) {
			if (err?.response?.status === 404) {
				// Authoritative: nothing is active right now. A failed stored run
				// still gets one more look (it's deliberately excluded from
				// "active" — see decideRunDiscovery) so "Try again" survives a
				// reload; anything else (completed/cancelled/no stored run) clears.
				const decision = decideRunDiscovery('not_found', cachedRun);
				if (decision.show === 'stored-failed' && cachedRun) {
					run = cachedRun;
				} else {
					forgetRunId();
					run = null;
				}
			} else if (!run) {
				runFetchError = "Couldn't check for an in-progress setup — trying again shortly.";
			}
		} finally {
			runChecked = true;
		}
	}

	/** Quiet escape hatch from a failed run that's no longer going anywhere on
	 * its own — clears the stored id and drops back to the recipe catalog. */
	function startOver() {
		clearPoll();
		forgetRunId();
		run = null;
	}

	async function retryRun() {
		if (!run || retryBusy) return;
		const runId = run.id;
		const previous = run;
		// Optimistic flip so the failed state doesn't linger while the request
		// is in flight.
		run = { ...run, status: 'running', error_code: null, safe_error_detail: null };
		retryBusy = true;
		retryError = '';
		try {
			const updated = await api.applySetupRunAction(runId, 'retry_step');
			run = updated;
			schedulePoll(updated.id);
		} catch (err: any) {
			run = previous;
			retryError = err?.response?.data?.detail || err?.message || "Couldn't start the retry.";
		} finally {
			retryBusy = false;
		}
	}

	// --- start-a-recipe -----------------------------------------------------

	let recipes: SetupRecipe[] | null = null;
	let recipesLoading = false;
	let recipesError = '';
	let startingRecipeId: string | null = null;
	let startError = '';

	async function loadRecipes() {
		recipesLoading = true;
		recipesError = '';
		try {
			const result = await api.getSetupRecipes();
			recipes = result.recipes;
		} catch (err: any) {
			recipesError =
				err?.response?.data?.detail?.message ||
				err?.response?.data?.detail ||
				err?.message ||
				"Couldn't load the setup recipes.";
		} finally {
			recipesLoading = false;
		}
	}

	// Load the catalog once we know there's no active run to show instead.
	$: if (isAdmin && runChecked && !run && recipes === null && !recipesLoading) loadRecipes();

	async function startRecipe(recipe: SetupRecipe) {
		if (startingRecipeId) return;
		startingRecipeId = recipe.id;
		startError = '';
		try {
			const created = await api.createSetupRun(recipe.id);
			run = created;
			rememberRunId(created.id);
			if (shouldPollRun(created.status)) schedulePoll(created.id);
		} catch (err: any) {
			startError = err?.response?.data?.detail || err?.message || "Couldn't start this recipe.";
		} finally {
			startingRecipeId = null;
		}
	}

	// --- consent -------------------------------------------------------------

	let consentBusy = false;
	let consentError = '';
	let cancelBusy = false;
	let cancelError = '';

	// Optional inline "add a provider API key" field the consent gate offers
	// when `consentRequest.providers` names one that isn't configured yet
	// (see `ArtifactsPlanExecutor._unconfigured_credential_providers`) —
	// keyed by provider id so more than one can be prompted for at once.
	let credentialDrafts: Record<string, string> = {};
	let credentialBusy: Record<string, boolean> = {};
	let credentialError: Record<string, string> = {};
	let credentialSaved: Record<string, boolean> = {};

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
				[provider.id]: err?.response?.data?.detail || err?.message || "Couldn't save the API key."
			};
		} finally {
			credentialBusy = { ...credentialBusy, [provider.id]: false };
		}
	}

	async function approveConsent() {
		if (!run || !consentGroup || consentBusy) return;
		consentBusy = true;
		consentError = '';
		try {
			const updated = await api.grantSetupRunConsent(run.id, consentGroup.stepKey);
			run = updated;
			if (shouldPollRun(updated.status)) schedulePoll(updated.id);
		} catch (err: any) {
			consentError = err?.response?.data?.detail || err?.message || "Couldn't approve the download.";
		} finally {
			consentBusy = false;
		}
	}

	async function cancelRun() {
		if (!run || cancelBusy) return;
		cancelBusy = true;
		cancelError = '';
		try {
			const updated = await api.applySetupRunAction(run.id, 'cancel');
			run = updated;
			clearPoll();
		} catch (err: any) {
			cancelError = err?.response?.data?.detail || err?.message || "Couldn't cancel setup.";
		} finally {
			cancelBusy = false;
		}
	}

	// --- first-generation handoff --------------------------------------

	function goToFirstGeneration() {
		if (generationHandoff) {
			const recipe = recipes?.find((r) => r.id === run?.recipe_id);
			const tabName = recipe ? recipe.name : 'First generation';
			tabsStore.addTabWithData(tabName, {
				selectedPreset: generationHandoff.presetId,
				selectedMode: generationHandoff.mode
			});
		}
		goto('/generate');
	}

	onMount(() => {
		load();
		if (isAdmin) discoverRun();
		else runChecked = true;
	});

	onDestroy(() => clearPoll());
</script>

<svelte:head>
	<title>Setup - PotionUI</title>
</svelte:head>

<div class="min-h-screen bg-canvas text-fg">
	<PageHeader title="Setup" description="Is this instance ready to generate?" sticky={false}>
		{#snippet actions()}
			<Button variant="secondary" icon="refresh" loading={refreshing} onclick={() => load(true)}>
				Refresh
			</Button>
		{/snippet}
	</PageHeader>

	<PageContainer width="sm" class="space-y-6">
		{#if loading}
			<div class="flex justify-center py-16">
				<Spinner size="lg" />
			</div>
		{:else if loadError}
			<EmptyState
				title="Couldn't load readiness"
				description={loadError}
				icon="warning"
			>
				{#snippet actions()}
					<Button variant="secondary" onclick={() => load()}>Try again</Button>
				{/snippet}
			</EmptyState>
		{:else if report}
			<div>
				<h1 class="text-xl font-semibold text-fg">{readinessHeadline(report)}</h1>
			</div>

			{#if isAdmin && runChecked && run}
				<Card class="space-y-4">
					<div class="flex items-start justify-between gap-3">
						<div class="min-w-0">
							<h2 class="text-sm font-semibold text-fg">Guided setup</h2>
							<p class="text-sm text-fg-muted mt-0.5">{runManifestProgressSummary(resolvedSteps)}</p>
						</div>
						<Badge variant={runBadgeVariant(run.status)}>{runStatusLabel(run.status)}</Badge>
					</div>

					{#if runFetchError}
						<p class="text-xs text-fg-subtle">{runFetchError}</p>
					{/if}

					{#if run.status === 'completed'}
						<!-- first-generation handoff -->
						<div class="rounded-lg border border-success/25 bg-success/5 px-4 py-5 text-center">
							<p class="text-sm font-semibold text-success">You're all set</p>
							<p class="text-sm text-fg-muted mt-1">
								This instance is ready to generate. Your recipe installed everything it needed.
							</p>

							{#if smokeThumbnailUrl}
								<div class="mt-4 flex flex-col items-center gap-2">
									<img
										src={smokeThumbnailUrl}
										alt="Output from your setup's test generation"
										class="max-h-56 rounded-lg border border-line shadow-raised"
									/>
									<p class="text-xs text-fg-subtle">Here's the test image your setup produced.</p>
								</div>
							{/if}

							<div class="mt-4">
								<Button variant="primary" icon="sparkles" onclick={goToFirstGeneration}>
									Create your first image
								</Button>
							</div>
						</div>
					{/if}

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
							<div class="mt-2">
								<button
									type="button"
									class="text-xs text-fg-subtle hover:text-fg-muted underline decoration-dotted"
									onclick={startOver}
								>
									Start over with a different recipe instead
								</button>
							</div>
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
										{#if isAdmin && group.kind === 'artifacts.fetch'}
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
											<summary class="text-sm text-danger cursor-pointer">
												Why it didn't finish
											</summary>
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
				</Card>
			{:else if isAdmin && runChecked && !run}
				<!-- Start-a-recipe: no active run, offer the recipe catalog. -->
				<div class="space-y-3">
					<h2 class="text-sm font-semibold text-fg">Start guided setup</h2>

					{#if recipesLoading}
						<div class="flex justify-center py-10">
							<Spinner size="md" />
						</div>
					{:else if recipesError}
						<EmptyState title="Couldn't load setup recipes" description={recipesError} icon="warning">
							{#snippet actions()}
								<Button variant="secondary" onclick={loadRecipes}>Try again</Button>
							{/snippet}
						</EmptyState>
					{:else if recipes && recipes.length === 0}
						<EmptyState
							title="No setup recipes available"
							description="There's nothing to guide you through right now — check back after this instance is updated."
							icon="box"
						/>
					{:else if recipes}
						{#if startError}
							<p class="text-sm text-danger">{startError}</p>
						{/if}
						<div class="space-y-3">
							{#each recipes as recipe (recipe.id)}
								<Card class="space-y-2">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<h3 class="text-sm font-semibold text-fg">{recipe.name}</h3>
											<p class="text-sm text-fg-muted mt-0.5">{recipe.summary}</p>
											{#if recipe.description}
												<p class="text-xs text-fg-subtle mt-1 line-clamp-3">{recipe.description}</p>
											{/if}
										</div>
										<Button
											size="sm"
											variant={recipe.last_completed_at ? 'secondary' : 'primary'}
											loading={startingRecipeId === recipe.id}
											disabled={startingRecipeId !== null && startingRecipeId !== recipe.id}
											onclick={() => startRecipe(recipe)}
										>
											{recipe.last_completed_at ? 'Run again' : 'Start'}
										</Button>
									</div>
									<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle">
										{#if recipe.last_completed_at}
											<Badge size="sm" variant="success">Installed</Badge>
										{/if}
										<span class="font-mono">{recipe.engine}</span>
										{#if recipe.preset_name}
											<span>{recipe.preset_name}</span>
										{/if}
										{#if recipe.total_download_bytes != null}
											<span class="font-mono tabular-nums">
												~{formatBytes(recipe.total_download_bytes)} to download
											</span>
										{/if}
									</div>
								</Card>
							{/each}
						</div>
					{/if}
				</div>
			{/if}

			{#if report.overall === 'ready'}
				<Card padding="none" class="text-center py-10 px-6">
					<p class="text-sm text-fg-muted mb-4">
						This instance is generating successfully. You're all set.
					</p>
					<Button variant="primary" href="/generate">Go to Generate</Button>
				</Card>
			{:else}
				<div class="space-y-3">
					{#each report.checks as check (check.area)}
						{@const adminLink = readinessAdminLink(check.area)}
						<Card>
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<div class="flex items-center gap-2 mb-1">
										<h2 class="text-sm font-semibold text-fg">{readinessAreaLabel(check.area)}</h2>
										<Badge variant={readinessBadgeVariant(check.status)}>
											{check.status.replace('_', ' ')}
										</Badge>
									</div>
									<p class="text-sm text-fg-muted">{check.message}</p>
								</div>
							</div>
							{#if isAdmin && check.action}
								<div class="mt-3 pt-3 border-t border-line flex items-center justify-between gap-3">
									<p class="text-xs text-fg-subtle">{check.action}</p>
									{#if adminLink}
										<Button size="sm" variant="secondary" href={adminLink}>Open</Button>
									{/if}
								</div>
							{/if}
						</Card>
					{/each}
				</div>
			{/if}
		{/if}
	</PageContainer>
</div>
